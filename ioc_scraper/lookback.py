from __future__ import annotations

import calendar
from datetime import datetime, timezone
import re


LOOKBACK_PATTERN = re.compile(r"([1-9]\d*)m")


def _subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def parse_cutoff(
    now: datetime,
    lookback: str | None,
    since: str | None,
) -> datetime:
    if lookback and since:
        raise ValueError("lookback and since are mutually exclusive")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    if since:
        try:
            parsed = datetime.strptime(since, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("since must use YYYY-MM-DD") from exc
        return parsed.replace(tzinfo=timezone.utc)
    selected = lookback or "3m"
    match = LOOKBACK_PATTERN.fullmatch(selected)
    if not match:
        raise ValueError("lookback must be a positive number of months such as 3m")
    return _subtract_months(now, int(match.group(1)))
