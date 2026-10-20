from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from .io import Dataset


def clock_minutes(value: str | int | float) -> int:
    """read a clock time, including midnight as 24:00."""
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, str) and ":" in value:
            h, m = map(int, value.split(":"))
            if not 0 <= m < 60:
                raise ValueError
            minutes = h * 60 + m
        else:
            minutes = float(value) * 60
        if not 0 <= minutes <= 1440 or minutes != int(minutes):
            raise ValueError
        return int(minutes) % 1440
    except (TypeError, ValueError):
        raise ValueError(f"Invalid clock time: {value!r}. Use HH:MM.") from None


def period_windows(settings: dict) -> list[dict]:
    windows = settings.get("period_windows")
    if windows is None:
        windows = [{"name": "Light", "start": settings.get("light_on", 8), "end": settings.get("light_off", 20)},
                   {"name": "Dark", "start": settings.get("light_off", 20), "end": settings.get("light_on", 8)}]
    if not windows:
        raise ValueError("Add at least one day/night window")
    seen, occupied, result = set(), set(), []
    for w in windows:
        name = str(w.get("name", "")).strip()
        start, end = clock_minutes(w.get("start")), clock_minutes(w.get("end"))
        if not name or name in seen:
            raise ValueError("Day/night window names must be nonempty and unique")
        if start == end:
            raise ValueError("A day/night window must have different start and end times")
        minutes = {(start + i) % 1440 for i in range((end - start) % 1440)}
        if occupied.intersection(minutes):
            raise ValueError("Day/night windows must not overlap")
        occupied.update(minutes)
        seen.add(name)
        result.append({"name": name, "start": start, "end": end})
    return result


def observation_intervals(ds: Dataset) -> list[tuple[datetime, datetime]]:
    rows = ds.sessions.select("start", "end").rows() if ds.sessions is not None else [(ds.start, ds.end)]
    merged = []
    for start, end in sorted(rows):
        start, end = max(start, ds.start), min(end, ds.end)
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def observed_hours(intervals: list[tuple[datetime, datetime]], left: datetime, right: datetime) -> float:
    return sum(max(0., (min(end, right) - max(start, left)).total_seconds())
               for start, end in intervals) / 3600


def hour_bins(ds: Dataset, *, align: str = "clock", light_on: int = 8, light_off: int = 20) -> pl.DataFrame:
    left = ds.start if align == "phase" else ds.start.replace(minute=0, second=0, microsecond=0)
    origin = ds.start.replace(hour=0, minute=0, second=0, microsecond=0)
    intervals = observation_intervals(ds)
    rows = []
    while left < ds.end:
        right = min(left + timedelta(hours=1), ds.end)
        clock = left.hour
        light = light_on <= clock < light_off if light_on < light_off else clock >= light_on or clock < light_off
        hours = observed_hours(intervals, left, right)
        covered = (min(right, ds.end) - max(left, ds.start)).total_seconds()
        rows.append({"bin": len(rows), "datetime": left, "window_start": left, "window_end": right,
                     "hour": len(rows) if align == "phase" else int((left - origin).total_seconds() // 3600),
                     "clock_hour": clock, "dark": not light,
                     "hours": hours, "window_hours": (right-left).total_seconds()/3600,
                     "recording_gap_s": max(0., round(covered-hours*3600, 9))})
        left += timedelta(hours=1)
    return pl.DataFrame(rows)


def period_bins(ds: Dataset, settings: dict) -> pl.DataFrame:
    windows = period_windows(settings)
    intervals = observation_intervals(ds)
    date = ds.start.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    rows = []
    while date < ds.end:
        for w in windows:
            left = date + timedelta(minutes=w["start"])
            right = date + timedelta(minutes=w["end"])
            if right <= left:
                right += timedelta(days=1)
            if left >= ds.end or right <= ds.start:
                continue
            hours = observed_hours(intervals, left, right)
            if hours == 0:
                continue
            rows.append({"bin": len(rows), "day": (date.date() - ds.start.date()).days + 1,
                         "period": w["name"], "window_start": left, "window_end": right,
                         "hours": hours, "phase_complete": left >= ds.start and right <= ds.end,
                         "recording_gap_s": max(0., round((min(right,ds.end)-max(left,ds.start)).total_seconds()-hours*3600, 9)),
                         "complete": left >= ds.start and right <= ds.end and abs(hours - (right-left).total_seconds()/3600) < 1e-9})
        date += timedelta(days=1)
    schema = {"bin": pl.Int64, "day": pl.Int64, "period": pl.Utf8, "window_start": pl.Datetime("us"),
              "window_end": pl.Datetime("us"), "hours": pl.Float64, "phase_complete": pl.Boolean, "recording_gap_s": pl.Float64, "complete": pl.Boolean}
    return pl.DataFrame(rows, schema=schema)
