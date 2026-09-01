from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status_code: int
    body: str
    content_type: str
    error_category: str = ""
    message: str = ""


BLOCK_MARKERS = (
    "access denied",
    "captcha",
    "verify you are human",
    "unusual traffic",
    "环境异常",
    "完成验证",
    "访问过于频繁",
)


def compact_message(value: object, limit: int = 300) -> str:
    return " ".join(str(value).split())[:limit]


def classify_block_page(status_code: int, body: str) -> str | None:
    folded = (body or "")[:20000].casefold()
    if status_code in {401, 403, 407}:
        return "blocked"
    if any(marker in folded for marker in BLOCK_MARKERS):
        return "blocked"
    return None


class HttpFetcher:
    def __init__(
        self,
        session=None,
        timeout: tuple[float, float] = (10.0, 30.0),
        retries: int = 2,
        user_agent: str = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        delay_seconds: float = 0.0,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise RuntimeError("Requests is required for live HTTP collection") from exc
            session = requests.Session()
        self.session = session
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.sleeper = sleeper

    def get(self, url: str) -> FetchResponse:
        last_message = ""
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                )
            except Exception as exc:
                last_message = compact_message(exc)
                if attempt < self.retries:
                    self.sleeper(min(2**attempt, 8))
                    continue
                return FetchResponse(url, 0, "", "", "network", last_message[:300])
            status = int(getattr(response, "status_code", 0))
            body = getattr(response, "text", "") or ""
            headers = getattr(response, "headers", {}) or {}
            final_url = getattr(response, "url", "") or url
            block = classify_block_page(status, body)
            retryable = status in {408, 429} or status >= 500
            if retryable and attempt < self.retries:
                retry_after = headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = min(2**attempt, 8)
                self.sleeper(delay)
                continue
            if block:
                return FetchResponse(final_url, status, body, headers.get("Content-Type", ""), "blocked", "access blocked")
            if status >= 400:
                return FetchResponse(final_url, status, body, headers.get("Content-Type", ""), "http", f"HTTP {status}")
            if self.delay_seconds:
                self.sleeper(self.delay_seconds)
            return FetchResponse(
                final_url,
                status,
                body,
                headers.get("Content-Type", "text/html"),
            )
        return FetchResponse(url, 0, "", "", "network", last_message[:300])


class BrowserFetcher:
    def __init__(self, playwright_factory=None, timeout: int = 30000, proxy: str = ""):
        self.playwright_factory = playwright_factory
        self.timeout = timeout
        self.proxy = proxy
        self._unavailable_message = ""

    def _factory(self):
        if self.playwright_factory is not None:
            return self.playwright_factory()
        from playwright.sync_api import sync_playwright

        return sync_playwright()

    def get(self, url: str, selector: str = "") -> FetchResponse:
        final_url = url
        if self._unavailable_message:
            return FetchResponse(
                url, 0, "", "", "unsupported", self._unavailable_message
            )
        try:
            with self._factory() as playwright:
                launch_options = {"headless": True}
                if self.proxy:
                    launch_options["proxy"] = {"server": self.proxy}
                browser = playwright.chromium.launch(**launch_options)
                try:
                    context = browser.new_context()
                    try:
                        page = context.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                        final_url = getattr(page, "url", "") or url
                        if selector:
                            page.wait_for_selector(selector, timeout=self.timeout)
                        body = page.content()
                        block = classify_block_page(200, body)
                        if block:
                            return FetchResponse(final_url, 200, body, "text/html", "blocked", "access blocked")
                        return FetchResponse(
                            final_url,
                            200,
                            body,
                            "text/html",
                        )
                    finally:
                        context.close()
                finally:
                    browser.close()
        except ImportError as exc:
            self._unavailable_message = compact_message(exc)
            return FetchResponse(
                url, 0, "", "", "unsupported", self._unavailable_message
            )
        except Exception as exc:
            message = compact_message(exc)
            folded = message.casefold()
            if "executable doesn't exist" in folded or "playwright install" in folded:
                self._unavailable_message = (
                    "Playwright Chromium is not installed; run: "
                    "python -m playwright install chromium"
                )
                return FetchResponse(
                    url, 0, "", "", "unsupported", self._unavailable_message
                )
            return FetchResponse(final_url, 0, "", "", "browser", message)
