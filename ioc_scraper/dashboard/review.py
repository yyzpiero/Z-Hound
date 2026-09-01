from __future__ import annotations

from pathlib import Path

from ..models import IndicatorRecord
from ..persistence.csv_repository import CsvRepository, iso_now
from .read_models import InvalidRunId, RunNotFound, _read_csv, list_runs, resolve_run_dir


ALLOWED_DECISIONS = {"accept", "reject"}


class DecisionConflict(RuntimeError):
    pass


class CandidateNotFound(LookupError):
    pass


def _row_to_record(row: dict[str, str], *, validation_status: str) -> IndicatorRecord:
    port_text = (row.get("port") or "").strip()
    port = int(port_text) if port_text else None
    source_text = (row.get("source_id") or "").strip()
    source_id = int(source_text) if source_text else 0
    return IndicatorRecord(
        id=row.get("id") or "",
        content=row.get("content") or "",
        type=row.get("type") or "",
        port=port,
        first_seen_at=row.get("first_seen_at") or "",
        last_seen_at=row.get("last_seen_at") or "",
        threat_actor=row.get("threat_actor") or "",
        threat_actor_source_name=row.get("threat_actor_source_name") or "",
        malware_family=row.get("malware_family") or "",
        indicator_role=row.get("indicator_role") or "",
        campaign=row.get("campaign") or "",
        confidence=row.get("confidence") or "",
        source_id=source_id,
        source_name=row.get("source_name") or "",
        report_title=row.get("report_title") or "",
        report_url=row.get("report_url") or "",
        published_at=row.get("published_at") or "",
        collected_at=row.get("collected_at") or "",
        last_checked_at=row.get("last_checked_at") or "",
        evidence=row.get("evidence") or "",
        extraction_method=row.get("extraction_method") or "",
        validation_status=validation_status,
        validation_reason=row.get("validation_reason") or "",
        first_run_id=row.get("first_run_id") or "",
        last_run_id=row.get("last_run_id") or "",
    )


def _decision_index(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row.get("run_id") or "", row.get("indicator_id") or "")
        if not key[0] or not key[1]:
            continue
        latest[key] = row
    return latest


def _candidate_rows(data_dir: Path, run_id: str | None = None) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if run_id:
        run_ids = [run_id]
    else:
        run_ids = [row["run_id"] for row in list_runs(data_dir)]
    for current in run_ids:
        try:
            run_dir = resolve_run_dir(data_dir, current)
        except (InvalidRunId, RunNotFound):
            continue
        for row in _read_csv(run_dir / "review.csv"):
            items.append({**row, "run_id": current})
    return items


def list_review(
    data_dir: Path,
    *,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    repository = CsvRepository(data_dir)
    decisions = _decision_index(repository.review_decision_rows())
    items = []
    for row in _candidate_rows(data_dir, run_id=run_id):
        current_run = row.get("run_id") or ""
        indicator_id = row.get("id") or ""
        recorded = decisions.get((current_run, indicator_id))
        decision = (recorded or {}).get("decision") or "pending"
        item = {
            "run_id": current_run,
            "indicator_id": indicator_id,
            "content": row.get("content") or "",
            "type": row.get("type") or "",
            "source_id": row.get("source_id") or "",
            "source_name": row.get("source_name") or "",
            "report_title": row.get("report_title") or "",
            "report_url": row.get("report_url") or "",
            "evidence": row.get("evidence") or "",
            "validation_reason": row.get("validation_reason") or "",
            "decision": decision,
            "note": (recorded or {}).get("note") or "",
            "decided_at": (recorded or {}).get("decided_at") or "",
        }
        if status and item["decision"] != status:
            continue
        items.append(item)
    return items


def decide(
    repository: CsvRepository,
    run_id: str,
    indicator_id: str,
    decision: str,
    note: str = "",
) -> dict:
    choice = (decision or "").strip().casefold()
    if choice not in ALLOWED_DECISIONS:
        raise ValueError("decision must be accept or reject")
    run_dir = resolve_run_dir(repository.data_dir, run_id)
    rows = _read_csv(run_dir / "review.csv")
    candidate = next((row for row in rows if row.get("id") == indicator_id), None)
    if candidate is None:
        raise CandidateNotFound(indicator_id)
    with repository.writer():
        existing = _decision_index(repository.review_decision_rows())
        previous = existing.get((run_id, indicator_id))
        if previous is not None:
            if (previous.get("decision") or "") == choice:
                return {
                    "run_id": run_id,
                    "indicator_id": indicator_id,
                    "decision": choice,
                    "note": previous.get("note") or "",
                    "idempotent": True,
                }
            raise DecisionConflict(
                f"candidate {indicator_id} already has decision {previous.get('decision')}"
            )
        if choice == "accept":
            record = _row_to_record(candidate, validation_status="accepted")
            repository.merge_indicators([record])
        repository.append_review_decision(
            {
                "decided_at": iso_now(repository.now()),
                "run_id": run_id,
                "indicator_id": indicator_id,
                "decision": choice,
                "note": note,
            }
        )
    return {
        "run_id": run_id,
        "indicator_id": indicator_id,
        "decision": choice,
        "note": note,
        "idempotent": False,
    }
