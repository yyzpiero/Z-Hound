from __future__ import annotations

import csv
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable, Iterable, Iterator

from ..models import IndicatorRecord, SourceStatus


INDICATOR_COLUMNS = [
    "id", "content", "type", "port", "first_seen_at", "last_seen_at",
    "threat_actor", "threat_actor_source_name", "malware_family",
    "indicator_role", "campaign", "confidence", "source_id", "source_name",
    "report_title", "report_url", "published_at", "collected_at",
    "last_checked_at", "evidence", "extraction_method",
    "validation_status", "validation_reason", "first_run_id", "last_run_id",
]
REPORT_COLUMNS = [
    "id", "source_id", "source_name", "url", "title", "published_at",
    "discovery_method", "publisher_name", "publisher_id", "cache_path",
    "fetch_state", "collected_at", "last_checked_at", "ioc_count",
]
SOURCE_STATUS_COLUMNS = [
    "source_id", "source_name", "strategy", "attempted_at", "status",
    "discovered_reports", "skipped_old_reports", "included_undated_reports",
    "error_category", "message", "final_url",
]
FAILURE_COLUMNS = [
    "source_id", "report_url", "attempted_at", "error_category", "message",
]
REVIEW_DECISION_COLUMNS = [
    "decided_at", "run_id", "indicator_id", "decision", "note",
]
CONFIDENCE = {"": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class MergeResult:
    added: int = 0
    duplicates: int = 0
    updated: int = 0
    added_ids: tuple[str, ...] = ()


def stable_id(parts: Iterable[object]) -> str:
    normalized = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CsvRepository:
    def __init__(
        self,
        data_dir: Path,
        now: Callable[[], datetime] = _utc_now,
    ):
        self.data_dir = Path(data_dir)
        self.now = now
        self._lock_depth = 0
        self._lock_handle = None
        self._thread_lock = threading.RLock()

    @contextmanager
    def writer(self) -> Iterator["CsvRepository"]:
        with self._thread_lock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield self
                finally:
                    self._lock_depth -= 1
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            handle = (self.data_dir / ".ioc-scraper.lock").open("a+")
            try:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("another IoC collector is writing this data directory") from exc
                self._lock_handle = handle
                self._lock_depth = 1
                yield self
            finally:
                if self._lock_depth:
                    self._lock_depth = 0
                if self._lock_handle is handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    self._lock_handle = None
                handle.close()

    @contextmanager
    def _write_scope(self):
        if self._lock_depth:
            yield
        else:
            with self.writer():
                yield

    def _read(self, name: str) -> list[dict[str, str]]:
        path = self.data_dir / name
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    def _atomic_write(
        self,
        name: str,
        columns: list[str],
        rows: Iterable[dict],
    ) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / name
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".tmp", dir=self.data_dir
        )
        try:
            with os.fdopen(
                descriptor, "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({column: row.get(column, "") for column in columns})
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
            raise

    def _atomic_text(self, name: str, content: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / name
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".tmp", dir=self.data_dir
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
            raise

    @staticmethod
    def _indicator_row(record: IndicatorRecord) -> dict:
        row = asdict(record)
        identity = stable_id(
            [
                record.type.casefold(),
                record.content.casefold(),
                "" if record.port is None else record.port,
                record.report_url,
            ]
        )
        row["id"] = identity
        row["port"] = "" if record.port is None else str(record.port)
        return row

    def available_run_id(self, base_run_id: str) -> str:
        candidate = base_run_id
        counter = 2
        while (self.data_dir / "runs" / candidate).exists():
            candidate = f"{base_run_id}_{counter:02d}"
            counter += 1
        return candidate

    def merge_indicators(
        self,
        records: Iterable[IndicatorRecord],
    ) -> MergeResult:
        with self._write_scope():
            existing_rows = self._read("indicators.csv")
            by_id = {row["id"]: row for row in existing_rows if row.get("id")}
            added = duplicates = updated = 0
            added_ids: list[str] = []
            for record in records:
                row = self._indicator_row(record)
                identity = row["id"]
                previous = by_id.get(identity)
                if previous is None:
                    by_id[identity] = row
                    added += 1
                    added_ids.append(identity)
                    continue
                duplicates += 1
                changed = False
                if not previous.get("first_run_id"):
                    previous["first_run_id"] = "legacy"
                    changed = True
                if row.get("last_run_id") and row["last_run_id"] != previous.get("last_run_id"):
                    previous["last_run_id"] = row["last_run_id"]
                    changed = True
                if row.get("last_checked_at") and row["last_checked_at"] != previous.get("last_checked_at"):
                    previous["last_checked_at"] = row["last_checked_at"]
                    changed = True
                if CONFIDENCE.get(row.get("confidence", ""), 0) > CONFIDENCE.get(previous.get("confidence", ""), 0):
                    for column in (
                        "first_seen_at", "last_seen_at", "threat_actor",
                        "threat_actor_source_name", "malware_family", "indicator_role",
                        "campaign", "confidence", "evidence", "extraction_method",
                    ):
                        if row.get(column):
                            previous[column] = row[column]
                    changed = True
                if changed:
                    updated += 1
            ordered = sorted(by_id.values(), key=lambda row: row["id"])
            self._atomic_write("indicators.csv", INDICATOR_COLUMNS, ordered)
            return MergeResult(added, duplicates, updated, tuple(added_ids))

    def merge_reports(self, records: Iterable[dict]) -> MergeResult:
        with self._write_scope():
            existing = self._read("reports.csv")
            by_id = {row["id"]: row for row in existing if row.get("id")}
            added = duplicates = updated = 0
            for incoming in records:
                row = dict(incoming)
                identity = row.get("id") or stable_id([row.get("url", "")])
                row["id"] = identity
                previous = by_id.get(identity)
                if previous is None:
                    by_id[identity] = row
                    added += 1
                    continue
                duplicates += 1
                original_collected = previous.get("collected_at", "")
                for column, value in row.items():
                    if value not in (None, "") and column != "collected_at":
                        previous[column] = value
                previous["collected_at"] = original_collected or row.get("collected_at", "")
                updated += 1
            self._atomic_write(
                "reports.csv", REPORT_COLUMNS, sorted(by_id.values(), key=lambda row: row["id"])
            )
            return MergeResult(added, duplicates, updated)

    def write_source_status(self, records: Iterable[SourceStatus]) -> None:
        with self._write_scope():
            rows = [asdict(record) for record in records]
            self._atomic_write("source_status.csv", SOURCE_STATUS_COLUMNS, rows)

    def append_failures(self, records: Iterable[dict]) -> None:
        with self._write_scope():
            rows = self._read("failures.csv") + [dict(record) for record in records]
            self._atomic_write("failures.csv", FAILURE_COLUMNS, rows)

    def write_run_artifacts(
        self,
        run_id: str,
        indicators: Iterable[IndicatorRecord],
        new_indicator_ids: Iterable[str],
        review: Iterable[IndicatorRecord],
        reports: Iterable[dict],
        statuses: Iterable[SourceStatus],
        failures: Iterable[dict],
        summary,
        log_lines: Iterable[str] = (),
    ) -> Path:
        with self._write_scope():
            run_dir = self.data_dir / "runs" / run_id
            run_repo = CsvRepository(run_dir, now=self.now)
            accepted_rows = [self._indicator_row(record) for record in indicators]
            review_rows = [self._indicator_row(record) for record in review]
            new_ids = set(new_indicator_ids)
            run_repo._atomic_write("indicators.csv", INDICATOR_COLUMNS, accepted_rows)
            run_repo._atomic_write(
                "new_indicators.csv",
                INDICATOR_COLUMNS,
                [row for row in accepted_rows if row["id"] in new_ids],
            )
            run_repo._atomic_write("review.csv", INDICATOR_COLUMNS, review_rows)
            run_repo._atomic_write("reports.csv", REPORT_COLUMNS, reports)
            run_repo._atomic_write(
                "source_status.csv",
                SOURCE_STATUS_COLUMNS,
                [asdict(status) for status in statuses],
            )
            run_repo._atomic_write("failures.csv", FAILURE_COLUMNS, failures)
            run_repo._atomic_text(
                "summary.json",
                json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            lines = list(log_lines)
            run_repo._atomic_text(
                "collector.log",
                "\n".join(lines) + ("\n" if lines else ""),
            )
            return run_dir

    def existing_report_urls(self) -> set[str]:
        return {row.get("url", "") for row in self._read("reports.csv") if row.get("url")}

    def report_rows(self) -> list[dict[str, str]]:
        return self._read("reports.csv")

    def indicator_rows(self) -> list[dict[str, str]]:
        return self._read("indicators.csv")

    def review_decision_rows(self) -> list[dict[str, str]]:
        return self._read("review_decisions.csv")

    def append_review_decision(self, record: dict) -> None:
        with self._write_scope():
            rows = self._read("review_decisions.csv") + [dict(record)]
            self._atomic_write("review_decisions.csv", REVIEW_DECISION_COLUMNS, rows)
