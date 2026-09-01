from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from .base import DiscoveryContext, DiscoveryResult, apply_lookback, response_body
from ..models import ReportCandidate


@dataclass
class _Link:
    href: str
    title: str
    in_article: bool
    published_at: str = ""


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[_Link] = []
        self._article_depth = 0
        self._article_start = 0
        self._article_date = ""
        self._anchor = None
        self._anchor_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "article":
            self._article_depth += 1
            if self._article_depth == 1:
                self._article_start = len(self.links)
                self._article_date = ""
        if tag == "a" and attributes.get("href"):
            self._anchor = (attributes["href"], self._article_depth > 0)
            self._anchor_text = []
        if tag == "time" and self._article_depth and attributes.get("datetime"):
            self._article_date = attributes["datetime"]
            for link in self.links[self._article_start :]:
                link.published_at = attributes["datetime"]

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor:
            href, in_article = self._anchor
            self.links.append(
                _Link(
                    href,
                    " ".join(self._anchor_text).strip(),
                    in_article,
                    self._article_date if in_article else "",
                )
            )
            self._anchor = None
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
            if not self._article_depth:
                self._article_date = ""

    def handle_data(self, data):
        if self._anchor:
            text = " ".join(data.split())
            if text:
                self._anchor_text.append(text)


RESEARCH_WORDS = re.compile(r"(?i)apt|threat|malware|research|report|analysis|campaign|security|威胁|研究|分析|报告")
NAVIGATION_WORDS = re.compile(r"(?i)about|contact|login|privacy|category|tag|首页|关于|登录")


class HtmlDiscovery:
    name = "html"

    def parse(self, content: str, context: DiscoveryContext) -> DiscoveryResult:
        parser = _Links()
        parser.feed(content)
        candidates = []
        source_host = urlsplit(context.source.url).hostname
        for link in parser.links:
            absolute = urljoin(context.source.url, link.href)
            parsed = urlsplit(absolute)
            if parsed.scheme not in {"http", "https"} or parsed.hostname != source_host:
                continue
            combined = f"{link.title} {parsed.path}"
            if NAVIGATION_WORDS.search(combined):
                continue
            if not link.in_article and not RESEARCH_WORDS.search(combined):
                continue
            if len(link.title) < 4 and not RESEARCH_WORDS.search(parsed.path):
                continue
            candidates.append(
                ReportCandidate(
                    context.source.id,
                    context.source.name,
                    absolute,
                    link.title,
                    link.published_at,
                    self.name,
                )
            )
        return apply_lookback(candidates, context.cutoff, context.max_reports)

    def discover(self, context: DiscoveryContext) -> DiscoveryResult:
        if context.session is None:
            return DiscoveryResult(status="unsupported", message="HTML fetcher unavailable")
        response = context.session.get(context.source.url)
        content, error = response_body(response)
        if error:
            status = "blocked" if error == "blocked" else "failed"
            return DiscoveryResult(
                status=status,
                error_category=error,
                message="listing request failed",
                final_url=getattr(response, "url", "") or context.source.url,
            )
        result = self.parse(content, context)
        result.final_url = getattr(response, "url", "") or context.source.url
        return result
