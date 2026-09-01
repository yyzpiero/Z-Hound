from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from contextlib import suppress
import os
from pathlib import Path
import tempfile
import time

from .extraction.active_time import extract_active_time
from .extraction.enrichment import AliasCatalog, enrich_indicator
from .extraction.indicators import extract_indicators
from .extraction.metadata import extract_page_metadata
from .lookback import parse_cutoff
from .fetcher import FetchResponse, compact_message
from .models import IndicatorRecord, RunSummary, SourceRecord, SourceStatus
from .persistence.csv_repository import CsvRepository, iso_now, stable_id
from .wechat import parse_wechat_article, wechat_url_kind


@dataclass(frozen=True)
class CollectOptions:
    lookback: str | None = None
    since: str | None = None
    selected_source_ids: frozenset[int] = frozenset()
    max_pages: int | None = None
    max_reports: int | None = None
    refresh: bool = False
    dry_run: bool = False


def build_run_id(now_value: datetime, options: CollectOptions) -> str:
    utc = now_value.astimezone(timezone.utc)
    timestamp = utc.strftime("%Y%m%dT%H%M%SZ")
    if options.lookback:
        scope = options.lookback.casefold()
    elif options.since:
        scope = f"since-{options.since}"
    else:
        scope = "all"
    safe_scope = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in scope
    )
    return f"{timestamp}_{safe_scope}"


class CollectorPipeline:
    def __init__(
        self,
        registry: list[SourceRecord],
        discovery,
        fetcher,
        repository: CsvRepository,
        cache_dir: Path,
        alias_catalog: AliasCatalog,
        progress=None,
        verbosity: int = 0,
        browser_fetcher=None,
        on_event=None,
    ):
        self.registry = list(registry)
        self.discovery = discovery
        self.fetcher = fetcher
        self.repository = repository
        self.cache_dir = Path(cache_dir)
        self.alias_catalog = alias_catalog
        self.progress = progress
        self.verbosity = verbosity
        self.browser_fetcher = browser_fetcher
        self.on_event = on_event
        self._log_lines: list[str] = []

    def _fetch_report(self, report) -> FetchResponse:
        response = self.fetcher.get(report.url)
        if (
            response.error_category == "blocked"
            and self.browser_fetcher is not None
            and wechat_url_kind(report.url) == "wechat_article"
        ):
            rendered = self.browser_fetcher.get(report.url, "#js_content")
            if rendered.error_category != "unsupported":
                return rendered
        return response

    def _emit(self, message: str, detail: int, timestamp: str) -> None:
        message = compact_message(message, 1000)
        self._log_lines.append(f"{timestamp} {message}")
        if self.progress is not None and detail <= self.verbosity:
            self.progress(message)

    def _save_cache(self, report_url: str, content: str, content_type: str) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".json" if "json" in content_type.casefold() else ".html"
        path = self.cache_dir / f"{stable_id([report_url])}{suffix}"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self.cache_dir
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
        return str(path)

    @staticmethod
    def _classification(candidate, enrichment) -> tuple[str, str]:
        if candidate.extraction_method in {"table", "ioc_section"}:
            return "accepted", "structured_ioc_content"
        return "review", "unstructured_document_scan"

    def _publish(self, event_type: str, timestamp: str, **fields) -> None:
        if self.on_event is None:
            return
        payload = {"type": event_type, "ts": timestamp, **fields}
        try:
            self.on_event(payload)
        except Exception:
            return

    def collect(self, options: CollectOptions) -> RunSummary:
        now_value = self.repository.now()
        if now_value.tzinfo is None:
            now_value = now_value.replace(tzinfo=timezone.utc)
        now_text = iso_now(now_value)
        run_id = self.repository.available_run_id(build_run_id(now_value, options))
        try:
            return self._collect(options, run_id, now_value, now_text)
        except Exception as exc:
            self._publish(
                "run_failed",
                now_text,
                run_id=run_id,
                message=compact_message(exc),
            )
            raise

    def _collect(
        self,
        options: CollectOptions,
        run_id: str,
        now_value: datetime,
        now_text: str,
    ) -> RunSummary:
        self._log_lines = []
        cutoff = parse_cutoff(now_value, options.lookback, options.since)
        existing_urls = self.repository.existing_report_urls()
        summary = RunSummary(run_id=run_id)
        statuses: list[SourceStatus] = []
        report_rows: list[dict] = []
        indicator_rows: list[IndicatorRecord] = []
        review_rows: list[IndicatorRecord] = []
        failures: list[dict] = []

        sources = [
            source
            for source in self.registry
            if not options.selected_source_ids or source.id in options.selected_source_ids
        ]
        scope = options.lookback or (f"since {options.since}" if options.since else "all")
        self._emit(
            f"run={run_id} start sources={len(sources)} scope={scope}",
            0,
            now_text,
        )
        self._publish(
            "run_started",
            now_text,
            run_id=run_id,
            sources=len(sources),
            scope=scope,
        )
        for source_index, source in enumerate(sources, start=1):
            source_started = time.monotonic()
            accepted_before = len(indicator_rows)
            review_before = len(review_rows)
            failures_before = len(failures)
            fetched_reports = 0
            if options.max_pages is not None:
                source = replace(source, max_pages=options.max_pages)
            if options.max_reports is not None:
                source = replace(source, max_reports=options.max_reports)
            summary.attempted_sources += 1
            self._publish(
                "source_started",
                now_text,
                run_id=run_id,
                source_id=source.id,
                source_name=source.name,
            )
            try:
                reports, status = self.discovery.discover(source, cutoff)
            except Exception as exc:
                status = SourceStatus(
                    source.id,
                    source.name,
                    source.strategy,
                    now_text,
                    "failed",
                    error_category="discovery",
                    message=compact_message(exc),
                )
                reports = []
            summary.discovered_reports += len(reports)
            summary.skipped_old_reports += status.skipped_old_reports
            summary.included_undated_reports += status.included_undated_reports
            source_failures = 0
            for report in reports:
                if report.url in existing_urls and not options.refresh:
                    continue
                response = self._fetch_report(report)
                if response.error_category:
                    source_failures += 1
                    summary.failures += 1
                    failures.append(
                        {
                            "source_id": source.id,
                            "report_url": report.url,
                            "attempted_at": now_text,
                            "error_category": response.error_category,
                            "message": compact_message(response.message),
                        }
                    )
                    self._emit(
                        f"report={report.url} state={response.error_category} "
                        f"message={compact_message(response.message)}",
                        1,
                        now_text,
                    )
                    self._publish(
                        "failure_recorded",
                        now_text,
                        run_id=run_id,
                        source_id=source.id,
                        error_category=response.error_category,
                    )
                    continue
                fetched_reports += 1
                cache_path = ""
                if not options.dry_run:
                    cache_path = self._save_cache(
                        report.url, response.body, response.content_type
                    )
                extraction_body = response.body
                if wechat_url_kind(report.url) == "wechat_article":
                    article = parse_wechat_article(response.body)
                    report = replace(
                        report,
                        title=report.title or article.title,
                        published_at=report.published_at or article.published_at,
                        publisher_name=(
                            report.publisher_name or article.publisher_name
                        ),
                        publisher_id=report.publisher_id or article.publisher_id,
                    )
                    extraction_body = article.content_html or response.body
                metadata = extract_page_metadata(
                    response.body,
                    response.content_type or "text/html",
                )
                if not report.title or not report.published_at:
                    report = replace(
                        report,
                        title=report.title or metadata.title,
                        published_at=report.published_at or metadata.published_at,
                    )
                candidates = extract_indicators(
                    extraction_body,
                    response.content_type or "text/html",
                )
                accepted_for_report = 0
                review_for_report = 0
                for candidate in candidates:
                    first_seen, last_seen, time_evidence = extract_active_time(
                        candidate.context
                    )
                    enrichment = enrich_indicator(
                        candidate, report, self.alias_catalog
                    )
                    validation_status, validation_reason = self._classification(
                        candidate, enrichment
                    )
                    evidence_parts = []
                    for value in (
                        enrichment.evidence,
                        time_evidence,
                        candidate.evidence,
                    ):
                        if value and value not in evidence_parts:
                            evidence_parts.append(value)
                    record = IndicatorRecord(
                        id="",
                        content=candidate.content,
                        type=candidate.type,
                        port=candidate.port,
                        first_seen_at=first_seen,
                        last_seen_at=last_seen,
                        threat_actor=enrichment.threat_actor,
                        threat_actor_source_name=enrichment.threat_actor_source_name,
                        malware_family=enrichment.malware_family,
                        indicator_role=enrichment.indicator_role,
                        campaign=enrichment.campaign,
                        confidence=enrichment.confidence,
                        source_id=source.id,
                        source_name=source.name,
                        report_title=report.title,
                        report_url=report.url,
                        published_at=report.published_at,
                        collected_at=now_text,
                        last_checked_at=now_text,
                        evidence=" | ".join(evidence_parts)[:500],
                        extraction_method=candidate.extraction_method,
                        validation_status=validation_status,
                        validation_reason=validation_reason,
                        first_run_id=run_id,
                        last_run_id=run_id,
                    )
                    if validation_status == "accepted":
                        indicator_rows.append(record)
                        accepted_for_report += 1
                        self._publish(
                            "indicator_accepted",
                            now_text,
                            run_id=run_id,
                            source_id=source.id,
                            indicator_type=candidate.type,
                        )
                    else:
                        review_rows.append(record)
                        review_for_report += 1
                        self._publish(
                            "indicator_reviewed",
                            now_text,
                            run_id=run_id,
                            source_id=source.id,
                            indicator_type=candidate.type,
                        )
                self._emit(
                    f"report={report.url} state=fetched",
                    1,
                    now_text,
                )
                self._emit(
                    f"report={report.url} extracted={len(candidates)} "
                    f"accepted={accepted_for_report} review={review_for_report}",
                    2,
                    now_text,
                )
                report_rows.append(
                    {
                        "id": stable_id([report.url]),
                        "source_id": source.id,
                        "source_name": source.name,
                        "url": report.url,
                        "title": report.title,
                        "published_at": report.published_at,
                        "discovery_method": report.discovery_method,
                        "publisher_name": report.publisher_name,
                        "publisher_id": report.publisher_id,
                        "cache_path": cache_path,
                        "fetch_state": "fetched",
                        "collected_at": now_text,
                        "last_checked_at": now_text,
                        "ioc_count": len(candidates),
                    }
                )
                existing_urls.add(report.url)
                self._publish(
                    "report_processed",
                    now_text,
                    run_id=run_id,
                    source_id=source.id,
                    accepted=accepted_for_report,
                    review=review_for_report,
                )
            if source_failures and status.status == "success":
                status = replace(
                    status,
                    status="partial",
                    error_category="report",
                    message=f"{source_failures} report fetches failed",
                )
            source_error_statuses = {
                "blocked",
                "failed",
                "layout_changed",
                "unsupported",
                "partial",
            }
            report_partial = (
                status.status == "partial" and status.error_category == "report"
            )
            if status.status in source_error_statuses and not report_partial:
                failures.append(
                    {
                        "source_id": source.id,
                        "report_url": status.final_url or source.url,
                        "attempted_at": status.attempted_at or now_text,
                        "error_category": (
                            status.error_category or status.status
                        ),
                        "message": compact_message(
                            status.message or status.status
                        ),
                    }
                )
                summary.failures += 1
                self._publish(
                    "failure_recorded",
                    now_text,
                    run_id=run_id,
                    source_id=source.id,
                    error_category=status.error_category or status.status,
                )
            statuses.append(status)
            elapsed = time.monotonic() - source_started
            diagnostic = f" method={status.strategy}"
            if status.status != "success":
                error_category = status.error_category or status.status
                reason = compact_message(
                    status.message or status.status
                ).replace(" ", "_")
                diagnostic += (
                    f" error={error_category} message={reason}"
                )
            self._emit(
                f"[{source_index}/{len(sources)}] {source.name} "
                f"status={status.status} reports={len(reports)} fetched={fetched_reports} "
                f"accepted={len(indicator_rows) - accepted_before} "
                f"review={len(review_rows) - review_before} "
                f"failures={len(failures) - failures_before} elapsed={elapsed:.1f}s"
                f"{diagnostic}",
                0,
                now_text,
            )
            self._publish(
                "source_completed",
                now_text,
                run_id=run_id,
                source_id=source.id,
                source_name=source.name,
                status=status.status,
                reports=len(reports),
            )

        for status in statuses:
            summary.source_status_counts[status.status] = (
                summary.source_status_counts.get(status.status, 0) + 1
            )

        unique_indicator_count = len(
            {
                (row.type, row.content.casefold(), row.port, row.report_url)
                for row in indicator_rows
            }
        )
        summary.review_indicators = len(
            {
                (row.type, row.content.casefold(), row.port, row.report_url)
                for row in review_rows
            }
        )
        summary.observed_indicators = unique_indicator_count
        if options.dry_run:
            summary.added_indicators = unique_indicator_count
            self._emit(
                f"run={run_id} dry-run complete accepted={unique_indicator_count} "
                f"review={summary.review_indicators}",
                0,
                now_text,
            )
            self._publish(
                "run_completed",
                now_text,
                run_id=run_id,
                dry_run=True,
                accepted=unique_indicator_count,
                review=summary.review_indicators,
            )
            return summary

        with self.repository.writer():
            self.repository.merge_reports(report_rows)
            merge = self.repository.merge_indicators(indicator_rows)
            self.repository.write_source_status(statuses)
            self.repository.append_failures(failures)
            summary.added_indicators = merge.added
            summary.duplicate_indicators = merge.duplicates
            summary.run_directory = str(self.repository.data_dir / "runs" / run_id)
            self._emit(
                f"run={run_id} complete new={merge.added} duplicates={merge.duplicates} "
                f"review={summary.review_indicators} failures={summary.failures}",
                0,
                now_text,
            )
            self.repository.write_run_artifacts(
                run_id=run_id,
                indicators=indicator_rows,
                new_indicator_ids=merge.added_ids,
                review=review_rows,
                reports=report_rows,
                statuses=statuses,
                failures=failures,
                summary=summary,
                log_lines=self._log_lines,
            )
        self._publish(
            "run_completed",
            now_text,
            run_id=run_id,
            accepted=summary.added_indicators,
            duplicates=summary.duplicate_indicators,
            review=summary.review_indicators,
            failures=summary.failures,
        )
        return summary
