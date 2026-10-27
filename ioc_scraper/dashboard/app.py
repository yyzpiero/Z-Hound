from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

from .. import __version__
from ..application import build_services, load_catalog, resolve_data_dir
from ..persistence.csv_repository import CsvRepository
from ..pipeline import CollectOptions
from ..validation import validate_sources
from .jobs import CollectionJobManager, JobConflict, NoActiveJob, iter_job_events
from .read_models import InvalidRunId, RunNotFound, list_runs, load_overview, load_run_detail, load_sources_view
from .review import CandidateNotFound, DecisionConflict, decide, list_review


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'"
    ),
}


def _json_error(status: int, code: str, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse({"error": code, "message": message}, status_code=status)


def _options_from_payload(payload: dict[str, Any]) -> CollectOptions:
    lookback = payload.get("lookback")
    since = payload.get("since")
    if lookback and since:
        raise ValueError("lookback and since cannot both be set")
    max_pages = payload.get("max_pages")
    max_reports = payload.get("max_reports")
    if max_pages is not None and int(max_pages) <= 0:
        raise ValueError("max_pages must be positive")
    if max_reports is not None and int(max_reports) <= 0:
        raise ValueError("max_reports must be positive")
    source_ids = payload.get("source") or payload.get("selected_source_ids") or []
    return CollectOptions(
        lookback=lookback or ("3m" if not since else None),
        since=since,
        selected_source_ids=frozenset(int(item) for item in source_ids),
        max_pages=int(max_pages) if max_pages is not None else None,
        max_reports=int(max_reports) if max_reports is not None else None,
        refresh=bool(payload.get("refresh", False)),
        dry_run=bool(payload.get("dry_run", False)),
    )


def _static_dir() -> Path:
    return Path(str(files("ioc_scraper.dashboard") / "static"))


def create_app(
    data_dir: Path | None = None,
    *,
    job_manager: CollectionJobManager | None = None,
    validator: Callable | None = None,
    version: str = __version__,
):
    from fastapi import FastAPI, Request
    from fastapi.exception_handlers import http_exception_handler
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    root = Path(data_dir).resolve() if data_dir is not None else resolve_data_dir()
    validate = validator or validate_sources
    manager_box: dict[str, CollectionJobManager | None] = {"manager": None}

    def runner(options: CollectOptions, emit) -> Any:
        selected = set(options.selected_source_ids) or None
        services = build_services(
            root,
            selected_ids=selected,
            progress=None,
            verbosity=-1,
        )
        services.pipeline.on_event = emit
        current_manager = manager_box["manager"]
        if current_manager is not None:
            services.pipeline.cancel_check = current_manager.is_cancelled
        return services.pipeline.collect(options)

    manager = job_manager or CollectionJobManager(runner)
    manager_box["manager"] = manager

    def job_busy() -> bool:
        current = manager.current()
        return bool(current and current.get("state") in {"running", "stopping"})

    static_dir = _static_dir()
    app = FastAPI(title="Z/HOUND", version=version, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def api_http_exception(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            message = exc.detail if isinstance(exc.detail, str) else "not found"
            return JSONResponse(
                {"error": "not_found", "message": message},
                status_code=exc.status_code,
            )
        return await http_exception_handler(request, exc)

    @app.get("/api/health")
    def health():
        accessible = root.exists() and root.is_dir()
        catalog = (root / "source.csv").is_file()
        return {
            "ok": True,
            "version": version,
            "data_dir_accessible": accessible,
            "catalog_present": catalog,
        }

    @app.get("/api/overview")
    def overview():
        payload = load_overview(root)
        payload["current_job"] = manager.current()
        return payload

    @app.get("/api/sources")
    def sources():
        rows = load_sources_view(root)
        current = manager.current()
        if current and current.get("state") in {"running", "stopping"}:
            live = {row["source_id"]: row for row in current.get("sources") or []}
            for row in rows:
                hit = live.get(row["source_id"])
                if hit:
                    row["status"] = hit.get("status") or "running"
                    row["discovered_reports"] = int(hit.get("reports") or 0)
                    row["message"] = ""
                else:
                    row["status"] = "pending"
                    row["message"] = ""
        return {"sources": rows}

    @app.post("/api/sources/validate")
    def sources_validate(payload: dict[str, Any] | None = None):
        if job_busy():
            return _json_error(409, "job_active", "a collection job is already running")
        body = payload or {}
        try:
            selected = set(body.get("source") or []) or None
            strategies = set(body.get("strategy") or []) or None
            catalog = load_catalog(root, selected, strategies)
            max_pages = body.get("max_pages")
            max_reports = body.get("max_reports")
            if max_pages is not None or max_reports is not None:
                from dataclasses import replace

                catalog = [
                    replace(
                        source,
                        max_pages=int(max_pages) if max_pages is not None else source.max_pages,
                        max_reports=(
                            int(max_reports) if max_reports is not None else source.max_reports
                        ),
                    )
                    for source in catalog
                ]
            services = build_services(root, sources=catalog, with_pipeline=False)
            rows = validate(catalog, services.discovery)
        except (ValueError, FileNotFoundError) as exc:
            return _json_error(400, "invalid_request", str(exc))
        serialized = []
        for row in rows:
            if hasattr(row, "__dict__") and not isinstance(row, dict):
                serialized.append(
                    {
                        "source_id": getattr(row, "source_id", None),
                        "source_name": getattr(row, "source_name", ""),
                        "status": getattr(row, "status", ""),
                        "discovered_articles": getattr(row, "discovered_articles", 0),
                        "message": getattr(row, "message", ""),
                    }
                )
            else:
                serialized.append(row)
        return {"rows": serialized}

    @app.post("/api/runs", status_code=202)
    def start_run(payload: dict[str, Any] | None = None):
        try:
            options = _options_from_payload(payload or {})
            selected = set(options.selected_source_ids) or None
            load_catalog(root, selected)
            handle = manager.start(options)
        except (ValueError, FileNotFoundError) as exc:
            return _json_error(400, "invalid_request", str(exc))
        except JobConflict as exc:
            return _json_error(409, "job_active", str(exc))
        return {"run_id": handle.get("run_id"), "state": handle.get("state")}

    @app.get("/api/runs")
    def runs():
        return {"runs": list_runs(root)}

    @app.get("/api/runs/current")
    def current_run():
        current = manager.current()
        return current or {"run_id": None, "state": None}

    @app.post("/api/runs/current/stop", status_code=202)
    def stop_run():
        try:
            snapshot = manager.request_stop()
        except NoActiveJob as exc:
            return _json_error(404, "no_job", str(exc))
        return {"run_id": snapshot.get("run_id"), "state": snapshot.get("state")}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        try:
            return load_run_detail(root, run_id)
        except InvalidRunId:
            return _json_error(400, "invalid_run_id", "invalid run id")
        except RunNotFound:
            return _json_error(404, "run_not_found", "run not found")

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str):
        if run_id != "current":
            try:
                load_run_detail(root, run_id)
            except InvalidRunId:
                return _json_error(400, "invalid_run_id", "invalid run id")
            except RunNotFound:
                current = manager.current()
                if not current or current.get("run_id") not in {run_id, None}:
                    return _json_error(404, "run_not_found", "run not found")

        return StreamingResponse(
            iter_job_events(manager, run_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/review")
    def review(status: str | None = None, run_id: str | None = None):
        return {"items": list_review(root, status=status, run_id=run_id)}

    @app.post("/api/review/{run_id}/{indicator_id}/decision")
    def review_decision(run_id: str, indicator_id: str, payload: dict[str, Any] | None = None):
        body = payload or {}
        try:
            result = decide(
                CsvRepository(root),
                run_id,
                indicator_id,
                body.get("decision", ""),
                note=body.get("note") or "",
            )
        except InvalidRunId:
            return _json_error(400, "invalid_run_id", "invalid run id")
        except RunNotFound:
            return _json_error(404, "run_not_found", "run not found")
        except CandidateNotFound:
            return _json_error(404, "candidate_not_found", "candidate not found")
        except DecisionConflict as exc:
            return _json_error(409, "decision_conflict", str(exc))
        except ValueError as exc:
            return _json_error(400, "invalid_request", str(exc))
        return result

    @app.get("/")
    def index():
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    from ..hunting.api import install_routes

    install_routes(app, root)

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def serve(host: str = "127.0.0.1", port: int = 8080) -> int:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0
