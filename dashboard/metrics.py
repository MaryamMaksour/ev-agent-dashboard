"""Small helpers shared by the data sources: time parsing, percentiles, daily series, heatmaps."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable, Iterable, Optional

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_ts(value, naive_tz: tzinfo = timezone.utc) -> Optional[datetime]:
    """ISO text -> aware datetime. Text without a zone is read in `naive_tz`."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz)
    return dt


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def since(days: Optional[int], now: Optional[datetime] = None) -> Optional[datetime]:
    if not days:
        return None
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=days)


def percentile(values: Iterable[float], p: float) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def rnd(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def day_series(items: list, tz: tzinfo, days: Optional[int], value: Callable[[dict], float] = lambda i: 1,
               now: Optional[datetime] = None, max_days: int = 366) -> list:
    """One point per calendar day (in `tz`), including days with nothing, ending today."""
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    totals: Counter = Counter()
    for it in items:
        totals[it["ts"].astimezone(tz).date()] += value(it)
    if days:
        n = days
    elif items:
        first = min(it["ts"] for it in items).astimezone(tz).date()
        n = min((now.date() - first).days + 1, max_days)
    else:
        n = 7
    out = []
    for i in range(n - 1, -1, -1):
        d = now.date() - timedelta(days=i)
        out.append({"date": d.isoformat(), "label": f"{d.day} {d.strftime('%b')}", "weekday": WEEKDAYS[d.weekday()],
                    "value": round(totals.get(d, 0), 4)})
    return out


def heatmap(items: list, tz: tzinfo) -> dict:
    """Counts per weekday (Mon..Sun) x hour (0..23), in local time."""
    grid = [[0] * 24 for _ in range(7)]
    for it in items:
        t = it["ts"].astimezone(tz)
        grid[t.weekday()][t.hour] += 1
    peak = None
    best = 0
    for d in range(7):
        for h in range(24):
            if grid[d][h] > best:
                best, peak = grid[d][h], {"weekday": WEEKDAYS[d], "hour": h, "count": grid[d][h]}
    return {"weekdays": WEEKDAYS, "grid": grid, "peak": peak}


def top(counter: Counter, n: int = 10) -> list:
    return [{"name": k, "count": v} for k, v in counter.most_common(n)]
