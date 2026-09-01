from __future__ import annotations

from .base import DiscoveryContext, DiscoveryResult
from .html import HtmlDiscovery
from .wechat import WeChatDiscovery
from ..wechat import wechat_url_kind


class BrowserDiscovery:
    name = "browser"

    def __init__(self, browser_fetcher):
        self.browser_fetcher = browser_fetcher

    def discover(self, context: DiscoveryContext) -> DiscoveryResult:
        if self.browser_fetcher is None:
            return DiscoveryResult(status="unsupported", message="browser fetcher unavailable")
        response = self.browser_fetcher.get(context.source.url, context.source.list_selector)
        if response.error_category:
            status = "blocked" if response.error_category == "blocked" else "unsupported" if response.error_category == "unsupported" else "failed"
            return DiscoveryResult(
                status=status,
                error_category=response.error_category,
                message=response.message,
                final_url=response.url or context.source.url,
            )
        if wechat_url_kind(context.source.url):
            result = WeChatDiscovery(sleeper=lambda _seconds: None).parse(
                response.body, context
            )
        else:
            result = HtmlDiscovery().parse(response.body, context)
        result.final_url = response.url or context.source.url
        return result
