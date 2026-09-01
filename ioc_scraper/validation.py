from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from collections.abc import Sequence

from .discovery.base import canonicalize_url
from .fetcher import compact_message
from .models import SourceRecord, SourceStatus
from .wechat import canonicalize_wechat_url, wechat_url_kind


@dataclass(frozen=True)
class SourceValidation:
    source_id: int
    source_name: str
    canonical_url: str
    final_url: str
    source_type: str
    method: str
    status: str
    discovered_articles: int
    newest_published_at: str
    publisher_name: str
    publisher_id: str
    message: str


def validate_sources(sources: Sequence[SourceRecord], engine) -> list[SourceValidation]:
    rows: list[SourceValidation] = []
    for source in sources:
        try:
            reports, status = engine.discover(source, cutoff=None)
        except Exception as exc:
            reports = []
            status = SourceStatus(
                source_id=source.id,
                source_name=source.name,
                strategy=source.strategy,
                attempted_at="",
                status="failed",
                error_category="validation",
                message=compact_message(exc),
                final_url=source.url,
            )
        newest = max(
            (report.published_at for report in reports if report.published_at),
            default="",
        )
        publisher_name = next(
            (report.publisher_name for report in reports if report.publisher_name),
            "",
        )
        publisher_id = next(
            (report.publisher_id for report in reports if report.publisher_id),
            "",
        )
        wechat_type = wechat_url_kind(source.url)
        rows.append(
            SourceValidation(
                source_id=source.id,
                source_name=source.name,
                canonical_url=(
                    canonicalize_wechat_url(source.url)
                    if wechat_type
                    else canonicalize_url(source.url)
                ),
                final_url=status.final_url or source.url,
                source_type=wechat_type or "web",
                method=status.strategy,
                status=status.status,
                discovered_articles=len(reports),
                newest_published_at=newest,
                publisher_name=publisher_name,
                publisher_id=publisher_id,
                message=status.message,
            )
        )
    return rows


def format_validation_line(index: int, total: int, row: SourceValidation) -> str:
    values = (
        f"[{index}/{total}] {row.source_name}",
        f"status={row.status}",
        f"type={row.source_type or '-'}",
        f"method={row.method or '-'}",
        f"articles={row.discovered_articles}",
        f"newest={row.newest_published_at or '-'}",
        f"publisher={row.publisher_name or '-'}",
        f"account={row.publisher_id or '-'}",
        f"canonical={row.canonical_url or '-'}",
        f"final={row.final_url or '-'}",
        f"message={' '.join((row.message or '-').split())}",
    )
    return " ".join(values)


def write_validation_csv(path: Path, rows: Sequence[SourceValidation]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [field.name for field in fields(SourceValidation)]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
