from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from .base import DiscoveryContext, DiscoveryResult, apply_lookback, response_body
from ..models import ReportCandidate


class _AdvertisedFeeds(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        values = {key.casefold(): value for key, value in attrs}
        relation = (values.get("rel") or "").casefold()
        type_ = (values.get("type") or "").casefold()
        href = values.get("href") or ""
        if "alternate" in relation and href and (
            "rss" in type_ or "atom" in type_ or "xml" in type_
        ):
            self.urls.append(href)


def _text(element, names):
    for child in list(element):
        local = child.tag.rsplit("}", 1)[-1].casefold()
        if local in names:
            if local == "link" and child.attrib.get("href"):
                return child.attrib["href"].strip()
            return (child.text or "").strip()
    return ""


def _date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return value.strip()


class FeedDiscovery:
    name = "feed"

    def parse(self, content: str, context: DiscoveryContext) -> DiscoveryResult:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            return DiscoveryResult(status="failed", error_category="parse", message=str(exc))
        candidates = []
        for element in root.iter():
            local = element.tag.rsplit("}", 1)[-1].casefold()
            if local not in {"item", "entry"}:
                continue
            url = _text(element, {"link"})
            if not url:
                continue
            candidates.append(
                ReportCandidate(
                    context.source.id,
                    context.source.name,
                    url,
                    _text(element, {"title"}),
                    _date(_text(element, {"pubdate", "published", "updated", "date"})),
                    self.name,
                )
            )
        return apply_lookback(candidates, context.cutoff, context.max_reports)

    def discover(self, context: DiscoveryContext) -> DiscoveryResult:
        if context.session is None:
            return DiscoveryResult(status="unsupported", message="feed fetcher unavailable")
        response = context.session.get(context.source.url)
        content, error = response_body(response)
        if error:
            return DiscoveryResult(
                status="failed",
                error_category=error,
                message="feed request failed",
                final_url=getattr(response, "url", "") or context.source.url,
            )
        direct = self.parse(content, context)
        direct.final_url = getattr(response, "url", "") or context.source.url
        if direct.candidates:
            return direct
        parser = _AdvertisedFeeds()
        parser.feed(content)
        feed_urls = [urljoin(context.source.url, value) for value in parser.urls]
        if not feed_urls:
            feed_urls = [
                urljoin(context.source.url, path)
                for path in ("/feed", "/feed.xml", "/rss.xml")
            ]
        diagnostics = []
        for feed_url in feed_urls[: context.max_pages]:
            feed_response = context.session.get(feed_url)
            feed_content, feed_error = response_body(feed_response)
            if feed_error:
                diagnostics.append(feed_error)
                continue
            result = self.parse(feed_content, context)
            result.final_url = getattr(feed_response, "url", "") or feed_url
            if result.candidates:
                return result
            diagnostics.append(result.message or result.status)
        return DiscoveryResult(
            status="empty" if feed_urls else "unsupported",
            message="; ".join(diagnostics)[:300],
            final_url=getattr(response, "url", "") or context.source.url,
        )
