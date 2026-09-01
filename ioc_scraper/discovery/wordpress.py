from __future__ import annotations

import html
import json
from urllib.parse import urljoin

from .base import DiscoveryContext, DiscoveryResult, apply_lookback, response_body
from ..models import ReportCandidate


class WordPressDiscovery:
    name = "wordpress"

    def parse(self, content: str, context: DiscoveryContext) -> DiscoveryResult:
        try:
            rows = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            return DiscoveryResult(status="failed", error_category="parse", message=str(exc))
        if not isinstance(rows, list):
            return DiscoveryResult(status="failed", error_category="parse", message="WordPress response is not a list")
        candidates = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("link"):
                continue
            title = row.get("title", {})
            if isinstance(title, dict):
                title = title.get("rendered", "")
            candidates.append(
                ReportCandidate(
                    context.source.id,
                    context.source.name,
                    row["link"],
                    html.unescape(str(title)),
                    row.get("date_gmt") or row.get("date") or "",
                    self.name,
                )
            )
        return apply_lookback(candidates, context.cutoff, context.max_reports)

    def discover(self, context: DiscoveryContext) -> DiscoveryResult:
        if context.session is None:
            return DiscoveryResult(status="unsupported", message="WordPress fetcher unavailable")
        url = urljoin(context.source.url, "/wp-json/wp/v2/posts?per_page=100")
        response = context.session.get(url)
        content, error = response_body(response)
        if error:
            return DiscoveryResult(
                status="failed",
                error_category=error,
                message="WordPress request failed",
                final_url=getattr(response, "url", "") or url,
            )
        result = self.parse(content, context)
        result.final_url = getattr(response, "url", "") or url
        return result
