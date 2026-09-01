from __future__ import annotations

from datetime import datetime
import re


DATE_TOKEN = (
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{4}年\d{1,2}月\d{1,2}日|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})"
)


def _parse_date(value: str) -> str:
    cleaned = " ".join(value.strip().replace("年", "-").replace("月", "-").replace("日", "").split())
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y")
    for format_ in formats:
        try:
            return datetime.strptime(cleaned, format_).date().isoformat()
        except ValueError:
            continue
    return ""


RANGE_PATTERNS = (
    re.compile(rf"(?i)(?:active|observed)\s+from\s+({DATE_TOKEN})\s+(?:to|through|until)\s+({DATE_TOKEN})"),
    re.compile(rf"(?:活跃时间|活动时间).{{0,8}}?({DATE_TOKEN})\s*(?:至|到|—|~|～)\s*({DATE_TOKEN})"),
)
SINGLE_PATTERNS = (
    re.compile(rf"(?i)(?:first seen|last seen|active on|observed on)\s*[:：]?\s*({DATE_TOKEN})"),
    re.compile(rf"(?:首次发现(?:时间)?|最早发现(?:时间)?|最后发现(?:时间)?|最近发现(?:时间)?|活跃时间|活动时间)\s*[:：为]?\s*({DATE_TOKEN})"),
)


def extract_active_time(context: str) -> tuple[str, str, str]:
    for pattern in RANGE_PATTERNS:
        match = pattern.search(context)
        if match:
            first, last = _parse_date(match.group(1)), _parse_date(match.group(2))
            if first and last and first <= last:
                return first, last, match.group(0)[:500]
    for pattern in SINGLE_PATTERNS:
        match = pattern.search(context)
        if match:
            value = _parse_date(match.group(1))
            if value:
                return value, value, match.group(0)[:500]
    return "", "", ""
