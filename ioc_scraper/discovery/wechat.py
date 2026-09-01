from __future__ import annotations

from dataclasses import replace
import time
from typing import Callable

from .base import DiscoveryContext, DiscoveryResult, apply_lookback, response_body
from ..fetcher import classify_block_page, compact_message
from ..models import ReportCandidate
from ..wechat import (
    canonicalize_wechat_url,
    parse_wechat_article,
    parse_wechat_listing,
    wechat_album_identity,
    wechat_url_kind,
)


class WeChatDiscovery:
    name = "wechat"

    def __init__(self, sleeper: Callable[[float], None] = time.sleep):
        self.sleeper = sleeper

    def _status_for_filtered(
        self,
        filtered: DiscoveryResult,
        structure_recognized: bool,
        follow_urls: list[str],
        empty_confirmed: bool = False,
    ) -> DiscoveryResult:
        filtered.follow_urls = follow_urls
        if filtered.candidates:
            filtered.status = "success"
            return filtered
        if filtered.skipped_old:
            filtered.status = "no_recent_reports"
            filtered.error_category = "lookback"
            filtered.message = "all dated reports are older than the cutoff"
            return filtered
        if structure_recognized and empty_confirmed:
            filtered.status = "empty"
            filtered.error_category = "empty"
            filtered.message = "valid WeChat page contains no articles"
            return filtered
        filtered.status = "layout_changed"
        filtered.error_category = "layout"
        filtered.message = "recognized WeChat article structure not found"
        return filtered

    def parse(self, content: str, context: DiscoveryContext) -> DiscoveryResult:
        if classify_block_page(200, content):
            return DiscoveryResult(
                status="blocked",
                error_category="blocked",
                message="WeChat verification page",
            )
        kind = wechat_url_kind(context.source.url)
        if kind == "wechat_article":
            article = parse_wechat_article(content)
            recognized = bool(
                article.title
                or article.published_at
                or article.publisher_name
                or article.publisher_id
                or article.content_html
            )
            candidates = []
            canonical = canonicalize_wechat_url(context.source.url)
            if recognized and canonical:
                candidates.append(
                    ReportCandidate(
                        source_id=context.source.id,
                        source_name=context.source.name,
                        url=canonical,
                        title=article.title,
                        published_at=article.published_at,
                        discovery_method=self.name,
                        publisher_name=article.publisher_name,
                        publisher_id=article.publisher_id,
                    )
                )
            follow_urls = [article.album_url] if article.album_url else []
            filtered = apply_lookback(
                candidates, context.cutoff, context.max_reports
            )
            return self._status_for_filtered(filtered, recognized, follow_urls)

        listing = parse_wechat_listing(content, context.source.url)
        candidates = [
            ReportCandidate(
                source_id=context.source.id,
                source_name=context.source.name,
                url=item.url,
                title=item.title,
                published_at=item.published_at,
                discovery_method=self.name,
                publisher_name=item.publisher_name,
                publisher_id=item.publisher_id,
            )
            for item in listing.items
        ]
        filtered = apply_lookback(candidates, context.cutoff, context.max_reports)
        return self._status_for_filtered(
            filtered,
            listing.structure_recognized,
            list(listing.follow_urls),
            listing.empty_confirmed,
        )

    def discover(self, context: DiscoveryContext) -> DiscoveryResult:
        if context.session is None:
            return DiscoveryResult(
                status="unsupported",
                error_category="unsupported",
                message="WeChat HTTP fetcher unavailable",
                final_url=context.source.url,
            )

        start_url = canonicalize_wechat_url(context.source.url)
        if not start_url:
            return DiscoveryResult(
                status="unsupported",
                error_category="source_url",
                message="unsupported WeChat source URL",
                final_url=context.source.url,
            )
        queue = [start_url]
        seen_pages: set[str] = set()
        exposed_follow: dict[str, None] = {}
        combined: list[ReportCandidate] = []
        failures: list[tuple[str, str, str]] = []
        structure_recognized = False
        final_url = start_url
        source_kind = wechat_url_kind(start_url)
        source_album_identity = wechat_album_identity(start_url)

        while queue and len(seen_pages) < context.max_pages:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            if seen_pages and context.source.delay_seconds:
                self.sleeper(context.source.delay_seconds)
            seen_pages.add(page_url)
            response = context.session.get(page_url)
            if len(seen_pages) == 1:
                final_url = getattr(response, "url", "") or page_url
            content, error = response_body(response)
            if error:
                failures.append(
                    (
                        error,
                        compact_message(getattr(response, "message", "") or error),
                        getattr(response, "url", "") or page_url,
                    )
                )
                continue

            page_source = replace(context.source, url=page_url)
            page_context = DiscoveryContext(
                source=page_source,
                cutoff=None,
                session=context.session,
                max_pages=context.max_pages,
                max_reports=context.max_reports,
            )
            parsed = self.parse(content, page_context)
            if parsed.status == "blocked":
                failures.append(("blocked", parsed.message, page_url))
                continue
            if parsed.status != "layout_changed":
                structure_recognized = True
            combined.extend(parsed.candidates)
            for follow_url in parsed.follow_urls:
                canonical = canonicalize_wechat_url(
                    follow_url, keep_pagination=True
                )
                if not canonical:
                    continue
                if (
                    source_kind == "wechat_album"
                    and wechat_album_identity(canonical)
                    != source_album_identity
                ):
                    continue
                exposed_follow[canonical] = None
                if (
                    source_kind == "wechat_album"
                    and canonical not in seen_pages
                    and canonical not in queue
                ):
                    queue.append(canonical)
            if source_kind == "wechat_article":
                break
            if len({item.url for item in combined}) >= context.max_reports:
                break

        filtered = apply_lookback(
            combined, context.cutoff, context.max_reports
        )
        filtered.follow_urls = list(exposed_follow)
        filtered.final_url = final_url
        if filtered.candidates:
            if failures:
                category, message, failed_url = failures[0]
                filtered.status = "partial"
                filtered.error_category = category
                filtered.message = (
                    f"{failed_url}: {category}: {message}"
                )[:300]
            else:
                filtered.status = "success"
            return filtered
        if filtered.skipped_old:
            filtered.status = "no_recent_reports"
            filtered.error_category = "lookback"
            filtered.message = "all dated reports are older than the cutoff"
            return filtered
        if failures:
            category, message, failed_url = failures[0]
            filtered.status = "blocked" if category == "blocked" else "failed"
            filtered.error_category = category
            filtered.message = f"{failed_url}: {category}: {message}"[:300]
            return filtered
        if structure_recognized:
            filtered.status = "empty"
            filtered.error_category = "empty"
            filtered.message = "valid WeChat page contains no articles"
            return filtered
        filtered.status = "layout_changed"
        filtered.error_category = "layout"
        filtered.message = "recognized WeChat article structure not found"
        return filtered
