from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .schemas import AttentionItem, Overview, RunDetail, RunSummaryView, SourceView


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
NOMINAL_STATUSES = {"success", "no_recent_reports", "empty"}
ALERT_LIMIT = 8
VENDOR_EN = {
    "奇安信": "QiAnXin",
    "微步在线": "ThreatBook",
    "安天": "Antiy",
    "绿盟": "NSFOCUS",
    "深信服": "Sangfor",
    "知道创宇": "Knownsec",
    "安恒": "Dbappsecurity",
    "华安云科": "BaiZe Lab",
    "金山毒霸": "Kingsoft",
    "希潭实验室": "Xitan Lab",
    "山石网科安全技术研究院": "Hillstone",
    "山石网科": "Hillstone",
    "观成科技": "Observer",
    "腾讯安全": "Tencent Security",
    "赛门铁克": "Symantec",
    "思科": "Cisco",
    "微软": "Microsoft",
    "卡巴斯基": "Kaspersky",
    "趋势科技": "Trend Micro",
}


def bilingual_labels(name: str, company: str = "") -> tuple[str, str]:
    name = (name or "").strip()
    company = (company or "").strip()
    has_chinese = bool(CJK_PATTERN.search(name) or CJK_PATTERN.search(company))
    if not has_chinese:
        text = name or company
        return text, text
    chinese = name if CJK_PATTERN.search(name) else company
    english = ""
    if company and not CJK_PATTERN.search(company):
        english = company
    elif name and not CJK_PATTERN.search(name):
        english = name
    if not english:
        english = VENDOR_EN.get(company) or VENDOR_EN.get(name) or ""
    if english and chinese and english.casefold() != chinese.casefold():
        return f"{english} ({chinese})", f"{chinese}（{english}）"
    text = english or chinese or name or company
    return text, text


class InvalidRunId(ValueError):
    pass


class RunNotFound(FileNotFoundError):
    pass


def _safe_int(value: object, default: int = 0) -> int:
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _safe_bool(value: object, default: bool = True) -> bool:
    cleaned = str(value or "").strip().casefold()
    if not cleaned:
        return default
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return default


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = []
            for row in csv.DictReader(handle):
                cleaned = {
                    str(key): "" if value is None else str(value)
                    for key, value in row.items()
                    if key is not None
                }
                if not any(value.strip("\x00 ").strip() for value in cleaned.values()):
                    continue
                rows.append(cleaned)
            return rows
    except (OSError, csv.Error, UnicodeError):
        return []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _runs_root(data_dir: Path) -> Path:
    return (Path(data_dir) / "runs").resolve()


def resolve_run_dir(data_dir: Path, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id or ""):
        raise InvalidRunId(run_id)
    root = _runs_root(data_dir)
    candidate = (root / run_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise InvalidRunId(run_id) from exc
    if candidate == root:
        raise InvalidRunId(run_id)
    if not candidate.is_dir():
        raise RunNotFound(run_id)
    return candidate


def _catalog_rows(data_dir: Path) -> list[dict[str, str]]:
    rows = []
    for row in _read_csv(Path(data_dir) / "source.csv"):
        try:
            source_id = int(str(row.get("id") or "").strip())
        except ValueError:
            continue
        rows.append({**row, "id": str(source_id)})
    return rows


def _status_by_source(data_dir: Path) -> dict[int, dict[str, str]]:
    mapping: dict[int, dict[str, str]] = {}
    for row in _read_csv(Path(data_dir) / "source_status.csv"):
        source_id = _safe_int(row.get("source_id"), default=-1)
        if source_id < 0:
            continue
        mapping[source_id] = row
    return mapping


def _summary_to_view(summary: dict[str, Any], run_id: str) -> RunSummaryView:
    counts = summary.get("source_status_counts") or {}
    state = "complete"
    if isinstance(counts, dict) and counts:
        if counts.get("failed") or counts.get("blocked"):
            state = "complete"
    lookback = ""
    if "_" in run_id:
        lookback = run_id.rsplit("_", 1)[-1]
    return {
        "run_id": str(summary.get("run_id") or run_id),
        "state": state,
        "attempted_sources": _safe_int(summary.get("attempted_sources")),
        "discovered_reports": _safe_int(summary.get("discovered_reports")),
        "added_indicators": _safe_int(summary.get("added_indicators")),
        "observed_indicators": _safe_int(summary.get("observed_indicators")),
        "review_indicators": _safe_int(summary.get("review_indicators")),
        "failures": _safe_int(summary.get("failures")),
        "lookback": lookback,
    }


def list_runs(data_dir: Path) -> list[RunSummaryView]:
    root = Path(data_dir) / "runs"
    if not root.is_dir():
        return []
    items: list[RunSummaryView] = []
    for child in root.iterdir():
        if not child.is_dir() or not RUN_ID_PATTERN.fullmatch(child.name):
            continue
        summary = _read_json(child / "summary.json")
        items.append(_summary_to_view(summary, child.name))
    items.sort(key=lambda row: row["run_id"], reverse=True)
    return items


def load_sources_view(data_dir: Path) -> list[SourceView]:
    statuses = _status_by_source(data_dir)
    views: list[SourceView] = []
    for row in _catalog_rows(data_dir):
        source_id = int(row["id"])
        status = statuses.get(source_id, {})
        name = (row.get("name") or "").strip()
        company = (row.get("company") or "").strip()
        label_en, label_zh = bilingual_labels(name, company)
        views.append(
            {
                "source_id": source_id,
                "name": name,
                "company": company,
                "label_en": label_en,
                "label_zh": label_zh,
                "strategy": (row.get("strategy") or "auto").strip() or "auto",
                "enabled": _safe_bool(row.get("enabled"), default=True),
                "status": (status.get("status") or "idle").strip() or "idle",
                "discovered_reports": _safe_int(status.get("discovered_reports")),
                "error_category": (status.get("error_category") or "").strip(),
                "message": (status.get("message") or "").strip(),
                "url": (row.get("url") or "").strip(),
            }
        )
    views.sort(key=lambda row: row["source_id"])
    return views


def _attention(sources: Iterable[SourceView]) -> list[AttentionItem]:
    items: list[AttentionItem] = []
    for source in sources:
        status = source.get("status", "")
        error = source.get("error_category") or status
        label = source.get("label_en") or source.get("name") or ""
        url = source.get("url") or ""
        detail = (source.get("message") or "").strip()
        if len(detail) > 160:
            detail = detail[:157] + "..."
        if status in {"blocked", "failed"} or error == "blocked":
            items.append(
                {
                    "priority": "P1" if status == "blocked" or error == "blocked" else "P2",
                    "code": "access_blocked" if status == "blocked" or error == "blocked" else "source_failed",
                    "title": label,
                    "label_en": source.get("label_en") or label,
                    "label_zh": source.get("label_zh") or source.get("name") or label,
                    "status": "blocked" if status == "blocked" or error == "blocked" else "failed",
                    "detail": detail or error or status,
                    "source_id": source["source_id"],
                    "url": url,
                }
            )
        elif status in {"layout_changed", "partial"} or error == "layout_changed":
            items.append(
                {
                    "priority": "P2",
                    "code": "layout_drift",
                    "title": label,
                    "label_en": source.get("label_en") or label,
                    "label_zh": source.get("label_zh") or source.get("name") or label,
                    "status": "inspect",
                    "detail": detail or error or status,
                    "source_id": source["source_id"],
                    "url": url,
                }
            )
    items.sort(key=lambda item: (0 if item["priority"] == "P1" else 1, item.get("source_id") or 0))
    return items[:ALERT_LIMIT]


MISSING_CATALOG_ALERT: AttentionItem = {
    "priority": "P1",
    "code": "catalog_missing",
    "title": "Source catalog missing",
    "label_en": "Source catalog missing",
    "label_zh": "缺少来源目录",
    "status": "blocked",
    "detail": "Copy data/source.example.csv to data/source.csv",
    "source_id": None,
    "url": "",
}


def _pending_review_count(data_dir: Path, latest_run: RunSummaryView | None) -> int:
    if latest_run is None:
        return 0
    try:
        run_dir = resolve_run_dir(data_dir, latest_run["run_id"])
    except (InvalidRunId, RunNotFound):
        return _safe_int(latest_run.get("review_indicators"))
    rows = _read_csv(run_dir / "review.csv")
    if rows:
        return len(rows)
    return _safe_int(latest_run.get("review_indicators"))


def load_overview(data_dir: Path) -> Overview:
    sources = load_sources_view(data_dir)
    runs = list_runs(data_dir)
    latest = runs[0] if runs else None
    enabled = [row for row in sources if row.get("enabled")]
    nominal = [
        row
        for row in enabled
        if row.get("status") in NOMINAL_STATUSES
    ]
    reports = _read_csv(Path(data_dir) / "reports.csv")
    indicators = _read_csv(Path(data_dir) / "indicators.csv")
    review_pending = _pending_review_count(data_dir, latest)
    metrics = {
        "enabled_sources": len(enabled),
        "nominal_sources": len(nominal),
        "reports_acquired": len(reports),
        "indicators_new": _safe_int(latest.get("added_indicators") if latest else 0),
        "indicators_observed": len(indicators),
        "review_pending": review_pending,
    }
    attention = _attention(enabled)
    if not (Path(data_dir) / "source.csv").is_file():
        attention = [MISSING_CATALOG_ALERT, *attention][:ALERT_LIMIT]
    return {
        "latest_run": latest,
        "current_job": None,
        "metrics": metrics,
        "attention": attention,
        "sources": sources,
    }


def load_run_detail(data_dir: Path, run_id: str) -> RunDetail:
    run_dir = resolve_run_dir(data_dir, run_id)
    summary = _read_json(run_dir / "summary.json")
    view = _summary_to_view(summary, run_id)
    review_rows = _read_csv(run_dir / "review.csv")
    report_rows = _read_csv(run_dir / "reports.csv")
    indicator_rows = _read_csv(run_dir / "indicators.csv")
    counts = summary.get("source_status_counts")
    if not isinstance(counts, dict):
        counts = {}
    return {
        **view,
        "review_count": len(review_rows) or view["review_indicators"],
        "reports": len(report_rows),
        "indicators": len(indicator_rows),
        "source_status_counts": {
            str(key): _safe_int(value) for key, value in counts.items()
        },
    }
