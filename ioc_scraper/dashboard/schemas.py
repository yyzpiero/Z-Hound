from __future__ import annotations

from typing import Any, TypedDict


class Metrics(TypedDict):
    enabled_sources: int
    nominal_sources: int
    reports_acquired: int
    indicators_new: int
    indicators_observed: int
    review_pending: int


class AttentionItem(TypedDict, total=False):
    priority: str
    code: str
    title: str
    label_en: str
    label_zh: str
    status: str
    detail: str
    source_id: int | None
    url: str


class RunSummaryView(TypedDict, total=False):
    run_id: str
    state: str
    attempted_sources: int
    discovered_reports: int
    added_indicators: int
    observed_indicators: int
    review_indicators: int
    failures: int
    lookback: str


class SourceView(TypedDict, total=False):
    source_id: int
    name: str
    company: str
    strategy: str
    enabled: bool
    status: str
    discovered_reports: int
    error_category: str
    message: str
    url: str
    label_en: str
    label_zh: str


class Overview(TypedDict, total=False):
    latest_run: RunSummaryView | None
    current_job: dict[str, Any] | None
    metrics: Metrics
    attention: list[AttentionItem]
    sources: list[SourceView]


class RunDetail(TypedDict, total=False):
    run_id: str
    state: str
    attempted_sources: int
    discovered_reports: int
    added_indicators: int
    observed_indicators: int
    review_indicators: int
    review_count: int
    failures: int
    source_status_counts: dict[str, int]
    reports: int
    indicators: int
