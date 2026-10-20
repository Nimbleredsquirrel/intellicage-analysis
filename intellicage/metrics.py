from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from .io import Dataset

CORNERS = [1, 2, 3, 4]
COUNTS = [f"{kind}_n" for kind in ("visits", "nosepokes", "licks")]
TOTALS = [f"{kind}_duration_total_s" for kind in ("visits", "nosepokes", "licks")]
IDS = ["group", "lot", "phase", "animal"]


def _ratio(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return pl.when(den > 0).then(num / den)


def _pct(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return _ratio(num, den) * 100


def _mice(ds: Dataset) -> pl.DataFrame:
    return ds.animals.select("Animal").unique().sort("Animal")


def _seconds_since(t0, col: pl.Expr) -> pl.Expr:
    return (col - pl.lit(t0)).dt.total_milliseconds() / 1000


def mouse_summary(ds: Dataset) -> pl.DataFrame:
    v = ds.visits.group_by("Animal").agg(
        visits_n=pl.len(),
        visits_duration_total_s=pl.col("VisitDuration").sum(),
        visits_duration_mean_s=pl.col("VisitDuration").mean(),
        visits_with_nosepoke_n=(pl.col("NosepokeNumber") > 0).sum(),
        visits_without_nosepoke_n=(pl.col("NosepokeNumber") == 0).sum(),
        licks_n=pl.col("LickNumber").sum(),
        licks_duration_total_s=pl.col("LickDuration").sum(),
        licks_contact_time_total_s=pl.col("LickContactTime").sum(),
    )
    n = ds.nosepokes.group_by("Animal").agg(
        nosepokes_n=pl.len(),
        nosepokes_duration_total_s=pl.col("NosepokeDuration").sum(),
        nosepokes_duration_mean_s=pl.col("NosepokeDuration").mean(),
        nosepokes_with_licks_n=(pl.col("LickNumber") > 0).sum(),
        nosepokes_without_licks_n=(pl.col("LickNumber") == 0).sum(),
        nosepokes_correct_n=(pl.col("SideCondition") == "Correct").sum(),
        nosepokes_incorrect_n=(pl.col("SideCondition") == "Incorrect").sum(),
    )
    df = (
        _mice(ds).join(v, on="Animal", how="left").join(n, on="Animal", how="left")
        .with_columns(pl.col(pl.UInt32, pl.Int64).fill_null(0))
        .with_columns(pl.col("visits_duration_total_s", "licks_duration_total_s", "licks_contact_time_total_s",
                              "nosepokes_duration_total_s").fill_null(0.))
        .with_columns(
            licks_duration_per_visit_s=_ratio(pl.col("licks_duration_total_s"), pl.col("visits_n")),
            licks_duration_mean_s=_ratio(pl.col("licks_duration_total_s"), pl.col("licks_n")),
            targeted_exploration_pct=_pct(pl.col("visits_with_nosepoke_n"), pl.col("visits_n")),
            time_in_corners_total_s=pl.col("visits_duration_total_s"),
        )
    )
    if ds.learning:
        scored = pl.col("nosepokes_correct_n") + pl.col("nosepokes_incorrect_n")
        df = df.with_columns(
            nosepokes_correct_pct=_pct(pl.col("nosepokes_correct_n"), scored),
            nosepokes_incorrect_pct=_pct(pl.col("nosepokes_incorrect_n"), scored),
            thirst_index=_ratio(pl.col("licks_n"), pl.col("nosepokes_correct_n")),
        )
    else:
        df = df.drop("nosepokes_correct_n", "nosepokes_incorrect_n")
    return ds.tag(df.rename({"Animal": "animal"}))


def corner_summary(ds: Dataset) -> pl.DataFrame:
    c = ds.visits.group_by("Animal", "Corner").agg(
        visits_n=pl.len(),
        time_s=pl.col("VisitDuration").sum(),
        visit_duration_mean_s=pl.col("VisitDuration").mean(),
    )
    df = (
        _mice(ds).join(pl.DataFrame({"Corner": CORNERS}), how="cross")
        .join(c, on=["Animal", "Corner"], how="left")
        .with_columns(pl.col("visits_n").fill_null(0), pl.col("time_s").fill_null(0.0))
        .with_columns(
            visits_pct=_pct(pl.col("visits_n"), pl.col("visits_n").sum().over("Animal")),
            time_pct=_pct(pl.col("time_s"), pl.col("time_s").sum().over("Animal")),
        )
        .sort("Animal", "Corner")
        .rename({"Animal": "animal", "Corner": "corner"})
    )
    return ds.tag(df)


def complete_days(ds: Dataset, min_hours: float) -> list[int]:
    from .timebins import observation_intervals, observed_hours

    intervals = observation_intervals(ds)
    n = math.ceil((ds.end - ds.start) / timedelta(days=1))
    return [d for d in range(1, n + 1)
            if observed_hours(intervals, ds.start + timedelta(days=d - 1),
                              ds.start + timedelta(days=d)) >= min_hours]


def learning_by_day(ds: Dataset, min_day_hours: float = 12) -> pl.DataFrame | None:
    if not ds.learning:
        return None
    d = (
        ds.nosepokes.with_columns(day=(_seconds_since(ds.start, pl.col("start")) // 86400 + 1).cast(pl.Int64))
        .group_by("Animal", "day")
        .agg(correct_n=(pl.col("SideCondition") == "Correct").sum(),
             incorrect_n=(pl.col("SideCondition") == "Incorrect").sum())
    )
    scored = pl.col("correct_n") + pl.col("incorrect_n")
    df = (
        _mice(ds).join(pl.DataFrame({"day": complete_days(ds, min_day_hours)}, schema={"day": pl.Int64}), how="cross")
        .join(d, on=["Animal", "day"], how="left")
        .with_columns(pl.col("correct_n", "incorrect_n").fill_null(0))
        .with_columns(correct_pct=_pct(pl.col("correct_n"), scored),
                      incorrect_pct=_pct(pl.col("incorrect_n"), scored),
                      day_label=pl.format("J{}", pl.col("day")))
        .sort("Animal", "day")
        .rename({"Animal": "animal"})
    )
    return ds.tag(df)


def _derived(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        *[_ratio(pl.col(f"{k}_duration_total_s"), pl.col(f"{k}_n")).alias(f"{k}_duration_mean_s")
          for k in ("visits", "nosepokes", "licks")],
        *[_ratio(pl.col(c), pl.col("hours")).alias(f"{c}_per_h") for c in COUNTS],
        *[_ratio(pl.col(f"{k}_duration_total_s"), pl.col("hours")).alias(f"{k}_duration_s_per_h")
          for k in ("visits", "nosepokes", "licks")],
    )


def _in_bins(events: pl.DataFrame, bins: pl.DataFrame) -> pl.DataFrame:
    return events.join_where(bins.select("bin", "window_start", "window_end"),
                             pl.col("start") >= pl.col("window_start"), pl.col("start") < pl.col("window_end"))


def _activity_in_bins(ds: Dataset, bins: pl.DataFrame) -> pl.DataFrame:
    # attribute each event to its start time
    v = _in_bins(ds.visits, bins).group_by("Animal", "bin").agg(
        visits_n=pl.len(), visits_duration_total_s=pl.col("VisitDuration").sum())
    n = _in_bins(ds.nosepokes, bins).group_by("Animal", "bin").agg(
        nosepokes_n=pl.len(), nosepokes_duration_total_s=pl.col("NosepokeDuration").sum(),
        licks_n=pl.col("LickNumber").sum(), licks_duration_total_s=pl.col("LickDuration").sum())
    df = (_mice(ds).join(bins, how="cross")
          .join(v, on=["Animal", "bin"], how="left").join(n, on=["Animal", "bin"], how="left")
          .with_columns(pl.col(COUNTS).fill_null(0), pl.col(TOTALS).fill_null(0.))
          .sort("Animal", "bin").rename({"Animal": "animal"}).drop("bin"))
    return ds.tag(_derived(df))


def activity_hourly(ds: Dataset, light_on: int = 8, light_off: int = 20, *, align: str = "clock") -> pl.DataFrame:
    from .timebins import hour_bins

    return _activity_in_bins(ds, hour_bins(ds, align=align, light_on=light_on, light_off=light_off))


def activity_circadian(hourly: pl.DataFrame) -> pl.DataFrame:
    df = hourly.group_by(*IDS, "clock_hour").agg(pl.col(COUNTS + TOTALS + ["hours"]).sum(), pl.col("dark").first())
    df = _derived(df).with_columns(*[pl.col(c).alias(f"{c}_total") for c in COUNTS])
    return df.with_columns(*[pl.col(f"{c}_per_h").alias(c) for c in COUNTS]).sort("group", "animal", "clock_hour")


def activity_periods(ds: Dataset, settings: dict) -> pl.DataFrame:
    from .timebins import period_bins

    return _activity_in_bins(ds, period_bins(ds, settings)).with_columns(day_label=pl.format("J{}", pl.col("day")))


def activity_light_dark(periods: pl.DataFrame, include_partial: bool = False) -> pl.DataFrame:
    if "period" not in periods.columns:
        periods = periods.with_columns(period=pl.when("dark").then(pl.lit("Dark")).otherwise(pl.lit("Light")))
    if "complete" in periods.columns and not include_partial:
        periods = periods.filter("complete")
    df = periods.group_by(*IDS, "period").agg(pl.col(COUNTS + TOTALS + ["hours"]).sum(), cycles_n=pl.len())
    return _derived(df).sort("group", "animal", "period")


def latency(ds: Dataset, window_min: float | None = None) -> pl.DataFrame:
    from .timebins import observation_intervals

    intervals = observation_intervals(ds)
    # a recording gap ends continuous follow-up
    recorded_until = min(ds.end, intervals[0][1] if intervals and intervals[0][0] <= ds.start else ds.start)
    stop = min(ds.start + timedelta(minutes=window_min), recorded_until) if window_min is not None else recorded_until
    window_s = max(0., (stop - ds.start).total_seconds())
    v = ds.visits.filter((pl.col("start") >= ds.start) & (pl.col("start") < stop)).sort("start", "VisitID")
    expressions = [pl.col("Corner").first().alias("first_corner")]
    for corner in [None, *CORNERS]:
        chosen = pl.lit(True) if corner is None else pl.col("Corner") == corner
        prefix = "first_visit" if corner is None else f"corner_{corner}"
        start = pl.col("start").filter(chosen).first()
        end = pl.col("end").filter(chosen).first()
        duration = pl.col("VisitDuration").filter(chosen).first()
        expressions.extend([
            _seconds_since(ds.start, start).alias(f"latency_{prefix}_s"),
            start.alias(f"{prefix}_start"), end.alias(f"{prefix}_end"),
            # keep the whole first visit, even when it ends after the chosen window
            pl.when(end <= recorded_until).then(duration).alias(f"{prefix}_duration_s"),
        ])
    first = v.group_by("Animal").agg(expressions)
    df = _mice(ds).join(first, on="Animal", how="left").with_columns(
        *[pl.col(f"latency_{prefix}_s").is_null().alias(f"latency_{prefix}_censored")
          for prefix in ["first_visit", *[f"corner_{c}" for c in CORNERS]]],
        window_s=pl.lit(window_s),
        followup_interrupted=pl.lit(recorded_until < ds.end),
    )
    return ds.tag(df.rename({"Animal": "animal"}))


def gate_sessions(ds: Dataset) -> pl.DataFrame | None:
    if ds.gate_sessions is None or ds.gate_sessions.is_empty():
        return None
    df = (
        ds.gate_sessions.group_by("Animal", "Start")
        .agg(end=pl.col("End").max(),
             weight_g=pl.col("Weight").filter(pl.col("Weight") > 0).mean(),
             directions=pl.col("Direction").sort().str.join("+"),
             paired=(pl.col("Direction") == "In").any() & (pl.col("Direction") == "Out").any())
        .rename({"Animal": "animal", "Start": "start"})
        .with_columns(duration_s=(pl.col("end") - pl.col("start")).dt.total_milliseconds() / 1000)
        .sort("animal", "start")
        .with_columns(session_n=pl.col("start").rank("ordinal").over("animal").cast(pl.Int64))
    )
    return ds.tag(df)


def gate_summary(ds: Dataset, sessions: pl.DataFrame | None, mouse_summary: pl.DataFrame) -> pl.DataFrame | None:
    if sessions is None:
        return None
    s = sessions.group_by("animal").agg(
        gate_sessions_n=pl.len(),
        gate_paired_sessions_n=pl.col("paired").sum(),
        gate_unpaired_sessions_n=(~pl.col("paired")).sum(),
        gate_first_session=pl.col("start").min(),
        gate_duration_mean_s=pl.col("duration_s").mean(),
        gate_duration_total_s=pl.col("duration_s").sum(),
        weight_mean_g=pl.col("weight_g").mean(),
    )
    df = (
        mouse_summary.select("animal", "visits_n")
        .join(s, on="animal", how="left")
        .with_columns(pl.col("gate_sessions_n", "gate_paired_sessions_n", "gate_unpaired_sessions_n").fill_null(0))
        .with_columns(gate_first_session_h=_seconds_since(ds.start, pl.col("gate_first_session")) / 3600,
                      visits_per_gate_session=_ratio(pl.col("visits_n"), pl.col("gate_sessions_n")))
        .sort("animal")
    )
    return ds.tag(df)


def gate_hourly(ds: Dataset, sessions: pl.DataFrame | None) -> pl.DataFrame | None:
    if sessions is None:
        return None
    from .timebins import hour_bins

    bins = hour_bins(ds, align="phase")
    # a long session belongs entirely to the hour in which it starts
    g = _in_bins(sessions, bins).group_by("animal", "bin").agg(
        gate_sessions_n=pl.len(), gate_duration_total_s=pl.col("duration_s").sum(),
        gate_duration_mean_s=pl.col("duration_s").mean(), gate_unpaired_sessions_n=(~pl.col("paired")).sum())
    df = (_mice(ds).rename({"Animal": "animal"}).join(bins, how="cross")
          .join(g, on=["animal", "bin"], how="left")
          .with_columns(pl.col("gate_sessions_n", "gate_unpaired_sessions_n").fill_null(0),
                        pl.col("gate_duration_total_s").fill_null(0.))
          .with_columns(gate_sessions_n_per_h=_ratio(pl.col("gate_sessions_n"), pl.col("hours")))
          .sort("animal", "bin").drop("bin"))
    return ds.tag(df)
