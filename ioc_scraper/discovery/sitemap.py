from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import DiscoveryContext, DiscoveryResult, apply_lookback, response_body
from ..models import ReportCandidate


class SitemapDiscovery:
    name = "sitemap"

    def parse(self, content: str, context: DiscoveryContext) -> DiscoveryResult:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            return DiscoveryResult(status="failed", error_category="parse", message=str(exc))
        local_root = root.tag.rsplit("}", 1)[-1].casefold()
        if local_root == "sitemapindex":
            urls = []
            for child in root.iter():
                if child.tag.rsplit("}", 1)[-1].casefold() == "loc" and child.text:
                    urls.append(child.text.strip())
            return DiscoveryResult(follow_urls=urls[: context.max_pages], status="success")
        candidates = []
        for url_element in list(root):
            if url_element.tag.rsplit("}", 1)[-1].casefold() != "url":
                continue
            values = {
                child.tag.rsplit("}", 1)[-1].casefold(): (child.text or "").strip()
                for child in list(url_element)
            }
            if values.get("loc"):
                candidates.append(
                    ReportCandidate(
                        context.source.id,
                        context.source.name,
                        values["loc"],
                        "",
                        values.get("lastmod", ""),
                        self.name,
                    )
                )
        return apply_lookback(candidates, context.cutoff, context.max_reports)

    def discover(self, context: DiscoveryContext) -> DiscoveryResult:
        if context.session is None:
            return DiscoveryResult(status="unsupported", message="sitemap fetcher unavailable")
        from urllib.parse import urljoin

        url = urljoin(context.source.url, "/sitemap.xml")
        response = context.session.get(url)
        content, error = response_body(response)
        if error:
            return DiscoveryResult(
                status="failed",
                error_category=error,
                message="sitemap request failed",
                final_url=getattr(response, "url", "") or url,
            )
        result = self.parse(content, context)
        result.final_url = getattr(response, "url", "") or url
        if result.follow_urls:
            combined = []
            final_url = result.final_url
            for child_url in result.follow_urls[: context.max_pages]:
                child_response = context.session.get(child_url)
                child_content, child_error = response_body(child_response)
                if child_error:
                    continue
                child_candidates = self.parse(child_content, context).candidates
                if child_candidates:
                    final_url = getattr(child_response, "url", "") or child_url
                combined.extend(child_candidates)
            combined_result = apply_lookback(
                combined, context.cutoff, context.max_reports
            )
            combined_result.final_url = final_url
            return combined_result
        return result
