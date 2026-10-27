from __future__ import annotations

import csv
from contextlib import suppress
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from .models import SourceRecord


CONFIG_COLUMNS = (
    "enabled",
    "strategy",
    "max_pages",
    "max_reports",
    "delay_seconds",
    "list_selector",
)
ALLOWED_STRATEGIES = {
    "auto",
    "feed",
    "sitemap",
    "wordpress",
    "html",
    "browser",
    "wechat",
    "antiy",
    "unsupported",
}


def _bool(value: str, default: bool = True) -> bool:
    cleaned = (value or "").strip().casefold()
    if not cleaned:
        return default
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def _positive_int(value: str, default: int, field: str) -> int:
    try:
        parsed = int(value) if (value or "").strip() else default
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def load_sources(
    path: Path,
    selected_ids: set[int] | None = None,
    selected_strategies: set[str] | None = None,
) -> list[SourceRecord]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"source catalog not found: {path}. "
            "Copy data/source.example.csv to data/source.csv"
        )
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    sources: list[SourceRecord] = []
    seen: set[int] = set()
    if selected_strategies is not None:
        selected_strategies = {
            strategy.strip().casefold() for strategy in selected_strategies
        }
        unknown_strategies = selected_strategies - ALLOWED_STRATEGIES
        if unknown_strategies:
            raise ValueError(
                "unknown strategy: " + ", ".join(sorted(unknown_strategies))
            )
    for row in rows:
        try:
            source_id = int((row.get("id") or "").strip())
        except ValueError as exc:
            raise ValueError("source id must be an integer") from exc
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        url = (row.get("url") or "").strip()
        parsed_url = urlsplit(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"invalid source URL for id {source_id}: {url}")
        enabled = _bool(row.get("enabled", ""), default=True)
        strategy = (row.get("strategy") or "auto").strip().casefold()
        if strategy not in ALLOWED_STRATEGIES:
            raise ValueError(f"unknown strategy for source {source_id}: {strategy}")
        if not enabled or (selected_ids is not None and source_id not in selected_ids):
            continue
        if selected_strategies is not None and strategy not in selected_strategies:
            continue
        delay_text = (row.get("delay_seconds") or "").strip()
        try:
            delay = float(delay_text) if delay_text else 1.0
        except ValueError as exc:
            raise ValueError(f"delay_seconds must be numeric for source {source_id}") from exc
        if delay < 0:
            raise ValueError(f"delay_seconds cannot be negative for source {source_id}")
        sources.append(
            SourceRecord(
                id=source_id,
                name=(row.get("name") or "").strip(),
                company=(row.get("company") or "").strip(),
                url=url,
                details=(row.get("details") or "").strip(),
                enabled=True,
                strategy=strategy,
                max_pages=_positive_int(row.get("max_pages", ""), 5, "max_pages"),
                max_reports=_positive_int(
                    row.get("max_reports", ""), 100, "max_reports"
                ),
                delay_seconds=delay,
                list_selector=(row.get("list_selector") or "").strip(),
            )
        )
    if selected_ids is not None:
        unknown_ids = selected_ids - seen
        if unknown_ids:
            raise ValueError(
                "unknown source id: " + ", ".join(str(item) for item in sorted(unknown_ids))
            )
    if (
        (selected_ids is not None or selected_strategies is not None)
        and not sources
    ):
        raise ValueError("no sources matched the selection")
    return sources


def upgrade_source_csv(path: Path) -> None:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    for column in CONFIG_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)
    defaults = {
        "enabled": "1",
        "strategy": "auto",
        "max_pages": "5",
        "max_reports": "100",
        "delay_seconds": "1.0",
        "list_selector": "",
    }
    for row in rows:
        for column, default in defaults.items():
            if not (row.get(column) or "").strip():
                row[column] = default
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="upgrade a source registry in place")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    upgrade_source_csv(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
