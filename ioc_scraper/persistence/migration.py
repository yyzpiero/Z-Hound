from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ..models import IndicatorRecord
from .csv_repository import CsvRepository, iso_now, stable_id


@dataclass(frozen=True)
class MigrationResult:
    imported_reports: int = 0
    imported_indicators: int = 0
    orphan_indicators: int = 0


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def migrate_legacy(data_dir: Path) -> MigrationResult:
    data_dir = Path(data_dir)
    repo = CsvRepository(data_dir)
    webpages = _read(data_dir / "webpage.csv")
    legacy_iocs = _read(data_dir / "webpage_ioc.csv")
    page_by_id = {row.get("id", ""): row for row in webpages}
    report_rows = []
    for row in webpages:
        url = row.get("url", "")
        if not url:
            continue
        collected = row.get("insert_time", "")
        report_rows.append(
            {
                "id": stable_id([url]),
                "source_id": row.get("source_id", ""),
                "source_name": "",
                "url": url,
                "title": row.get("title", ""),
                "published_at": row.get("release_date", ""),
                "discovery_method": "legacy",
                "cache_path": "",
                "fetch_state": "legacy",
                "collected_at": collected,
                "last_checked_at": row.get("last_crawl_time", ""),
                "ioc_count": row.get("ioc_num", ""),
            }
        )
    indicator_rows: list[IndicatorRecord] = []
    orphans = 0
    for row in legacy_iocs:
        page = page_by_id.get(row.get("webpage_id", ""))
        if not page or not row.get("content"):
            orphans += 1
            continue
        indicator_rows.append(
            IndicatorRecord(
                id="",
                content=row.get("content", ""),
                type=row.get("type", "") or "unknown",
                port=None,
                first_seen_at="",
                last_seen_at="",
                threat_actor="",
                threat_actor_source_name="",
                malware_family="",
                indicator_role="unknown",
                campaign="",
                confidence="low",
                source_id=int(page.get("source_id") or 0),
                source_name="",
                report_title=page.get("title", ""),
                report_url=page.get("url", ""),
                published_at=page.get("release_date", ""),
                collected_at=row.get("insert_time", "") or page.get("insert_time", ""),
                last_checked_at=page.get("last_crawl_time", ""),
                evidence="Imported from legacy CSV",
                extraction_method="legacy",
            )
        )
    with repo.writer():
        report_result = repo.merge_reports(report_rows)
        indicator_result = repo.merge_indicators(indicator_rows)
    return MigrationResult(
        imported_reports=report_result.added,
        imported_indicators=indicator_result.added,
        orphan_indicators=orphans,
    )


def export_legacy(data_dir: Path) -> None:
    data_dir = Path(data_dir)
    repo = CsvRepository(data_dir)
    reports = repo.report_rows()
    report_ids = {row["id"]: str(index + 1) for index, row in enumerate(reports)}
    webpage_columns = [
        "id", "title", "url", "source_id", "release_date", "insert_time",
        "last_crawl_time", "description", "provide_ioc", "threat_type",
        "threat_details", "ioc_num",
    ]
    webpage_rows = []
    for report in reports:
        webpage_rows.append(
            {
                "id": report_ids[report["id"]],
                "title": report.get("title", ""),
                "url": report.get("url", ""),
                "source_id": report.get("source_id", ""),
                "release_date": report.get("published_at", ""),
                "insert_time": report.get("collected_at", ""),
                "last_crawl_time": report.get("last_checked_at", ""),
                "description": "",
                "provide_ioc": "1" if report.get("ioc_count") not in {"", "0"} else "0",
                "threat_type": "",
                "threat_details": "",
                "ioc_num": report.get("ioc_count", ""),
            }
        )
    repo._atomic_write("webpage.csv", webpage_columns, webpage_rows)
    webpage_ioc_columns = ["id", "content", "type", "webpage_id", "insert_time"]
    webpage_ioc_rows = []
    report_id_by_url = {
        report.get("url", ""): report_ids[report["id"]] for report in reports
    }
    for index, indicator in enumerate(repo.indicator_rows(), start=1):
        webpage_id = report_id_by_url.get(indicator.get("report_url", ""))
        if not webpage_id:
            continue
        webpage_ioc_rows.append(
            {
                "id": str(index),
                "content": indicator.get("content", ""),
                "type": indicator.get("type", ""),
                "webpage_id": webpage_id,
                "insert_time": indicator.get("collected_at", ""),
            }
        )
    repo._atomic_write("webpage_ioc.csv", webpage_ioc_columns, webpage_ioc_rows)
