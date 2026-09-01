from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re

from ..models import ReportCandidate, SourceRecord


TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
NON_REPORT_PATH = re.compile(
    r"/(?:tag|tags|category|categories|author|search/label|page)/[^/]+/?$",
    re.I,
)


@dataclass(frozen=True)
class DiscoveryContext:
    source: SourceRecord
    cutoff: datetime | None
    session: object
    max_pages: int
    max_reports: int


@dataclass
class DiscoveryResult:
    candidates: list[ReportCandidate] = field(default_factory=list)
    follow_urls: list[str] = field(default_factory=list)
    status: str = "success"
    included_undated: int = 0
    skipped_old: int = 0
    error_category: str = ""
    message: str = ""
    final_url: str = ""


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        host = f"{host}:{port}"
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        folded = key.casefold()
        if folded.startswith("utm_") or folded in TRACKING_PARAMETERS:
            continue
        query.append((key, value))
    return urlunsplit((scheme, host, parsed.path or "/", urlencode(query), ""))


def is_report_url(url: str) -> bool:
    parsed = urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return False
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return NON_REPORT_PATH.search(path) is None


def _published_datetime(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            parsed = datetime.strptime(cleaned[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def apply_lookback(
    candidates: list[ReportCandidate],
    cutoff: datetime | None,
    max_reports: int,
    old_streak_limit: int = 10,
) -> DiscoveryResult:
    selected: list[ReportCandidate] = []
    seen: set[str] = set()
    old_streak = skipped = undated = 0
    for candidate in candidates:
        url = canonicalize_url(candidate.url)
        if not url or url in seen or not is_report_url(url):
            continue
        seen.add(url)
        published = _published_datetime(candidate.published_at)
        if published is None:
            undated += 1
            old_streak = 0
        elif cutoff and published < cutoff:
            skipped += 1
            old_streak += 1
            if old_streak >= old_streak_limit:
                break
            continue
        else:
            old_streak = 0
        selected.append(
            ReportCandidate(
                source_id=candidate.source_id,
                source_name=candidate.source_name,
                url=url,
                title=candidate.title,
                published_at=candidate.published_at,
                discovery_method=candidate.discovery_method,
                publisher_name=candidate.publisher_name,
                publisher_id=candidate.publisher_id,
            )
        )
        if len(selected) >= max_reports:
            break
    return DiscoveryResult(
        candidates=selected,
        status="success" if selected else "empty",
        included_undated=undated,
        skipped_old=skipped,
    )


def response_body(response: object) -> tuple[str, str]:
    if response is None:
        return "", "failed"
    error = getattr(response, "error_category", "")
    if error:
        return "", error
    body = getattr(response, "body", None)
    if body is None:
        body = getattr(response, "text", "")
    return body or "", ""
