from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from .base import DiscoveryContext, DiscoveryResult
from ..models import ReportCandidate, SourceRecord, SourceStatus
from ..fetcher import compact_message


SEVERITY = {
    "empty": 0,
    "no_recent_reports": 0,
    "unsupported": 1,
    "layout_changed": 2,
    "failed": 3,
    "partial": 4,
    "blocked": 5,
}


class DiscoveryEngine:
    def __init__(self, strategies, session=None):
        self.strategies = list(strategies)
        self.session = session

    def discover(
        self,
        source: SourceRecord,
        cutoff: datetime | None,
    ) -> tuple[list[ReportCandidate], SourceStatus]:
        context = DiscoveryContext(
            source,
            cutoff,
            self.session,
            source.max_pages,
            source.max_reports,
        )
        attempted = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        failures: list[tuple[str, DiscoveryResult]] = []
        selected = self.strategies
        host = (urlsplit(source.url).hostname or "").casefold()
        if host == "mp.weixin.qq.com" and source.strategy in {"auto", "wechat"}:
            by_name = {item.name: item for item in selected}
            selected = [
                by_name[name]
                for name in ("wechat", "browser")
                if name in by_name
            ]
        elif source.strategy != "auto":
            selected = [item for item in selected if item.name == source.strategy]
        else:
            if "antiy.cn" in host:
                selected = [
                    item
                    for item in selected
                    if item.name in {"antiy", "html", "browser"}
                ]
            else:
                selected = [
                    item
                    for item in selected
                    if item.name not in {"wechat", "antiy"}
                ]
        for strategy in selected:
            result = strategy.discover(context)
            if result.candidates:
                return result.candidates, SourceStatus(
                    source_id=source.id,
                    source_name=source.name,
                    strategy=strategy.name,
                    attempted_at=attempted,
                    status=(
                        "partial" if result.status == "partial" else "success"
                    ),
                    discovered_reports=len(result.candidates),
                    skipped_old_reports=result.skipped_old,
                    included_undated_reports=result.included_undated,
                    error_category=result.error_category,
                    message=result.message,
                    final_url=result.final_url or source.url,
                )
            if strategy.name == "wechat" and result.status in {
                "empty",
                "no_recent_reports",
                "blocked",
            }:
                return [], SourceStatus(
                    source_id=source.id,
                    source_name=source.name,
                    strategy=strategy.name,
                    attempted_at=attempted,
                    status=result.status,
                    discovered_reports=0,
                    skipped_old_reports=result.skipped_old,
                    included_undated_reports=result.included_undated,
                    error_category=result.error_category,
                    message=result.message,
                    final_url=result.final_url or source.url,
                )
            failures.append((strategy.name, result))
        if not failures:
            return [], SourceStatus(
                source.id, source.name, source.strategy, attempted, "unsupported",
                error_category="strategy", message="no matching discovery strategy",
                final_url=source.url,
            )
        strategy_name, result = max(
            failures, key=lambda pair: SEVERITY.get(pair[1].status, 0)
        )
        diagnostics = "; ".join(
            f"{name}:{value.status}"
            + (f"({compact_message(value.message)})" if value.message else "")
            for name, value in failures
        )[:300]
        return [], SourceStatus(
            source_id=source.id,
            source_name=source.name,
            strategy=strategy_name,
            attempted_at=attempted,
            status=result.status,
            discovered_reports=0,
            skipped_old_reports=result.skipped_old,
            included_undated_reports=result.included_undated,
            error_category=result.error_category,
            message=diagnostics,
            final_url=result.final_url or source.url,
        )
