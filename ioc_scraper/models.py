from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRecord:
    id: int
    name: str
    company: str
    url: str
    details: str = ""
    enabled: bool = True
    strategy: str = "auto"
    max_pages: int = 5
    max_reports: int = 100
    delay_seconds: float = 1.0
    list_selector: str = ""


@dataclass(frozen=True)
class ReportCandidate:
    source_id: int
    source_name: str
    url: str
    title: str = ""
    published_at: str = ""
    discovery_method: str = ""
    publisher_name: str = ""
    publisher_id: str = ""


@dataclass(frozen=True)
class FetchedReport:
    candidate: ReportCandidate
    content: str
    content_type: str
    fetched_at: str
    cache_path: str = ""


@dataclass(frozen=True)
class IndicatorCandidate:
    content: str
    type: str
    port: int | None
    evidence: str
    extraction_method: str
    context: str


@dataclass(frozen=True)
class IndicatorRecord:
    id: str
    content: str
    type: str
    port: int | None
    first_seen_at: str
    last_seen_at: str
    threat_actor: str
    threat_actor_source_name: str
    malware_family: str
    indicator_role: str
    campaign: str
    confidence: str
    source_id: int
    source_name: str
    report_title: str
    report_url: str
    published_at: str
    collected_at: str
    last_checked_at: str
    evidence: str
    extraction_method: str
    validation_status: str = "accepted"
    validation_reason: str = ""
    first_run_id: str = ""
    last_run_id: str = ""


@dataclass(frozen=True)
class SourceStatus:
    source_id: int
    source_name: str
    strategy: str
    attempted_at: str
    status: str
    discovered_reports: int = 0
    skipped_old_reports: int = 0
    included_undated_reports: int = 0
    error_category: str = ""
    message: str = ""
    final_url: str = ""


@dataclass
class RunSummary:
    run_id: str = ""
    run_directory: str = ""
    attempted_sources: int = 0
    source_status_counts: dict[str, int] = field(default_factory=dict)
    discovered_reports: int = 0
    skipped_old_reports: int = 0
    included_undated_reports: int = 0
    observed_indicators: int = 0
    added_indicators: int = 0
    duplicate_indicators: int = 0
    review_indicators: int = 0
    failures: int = 0
