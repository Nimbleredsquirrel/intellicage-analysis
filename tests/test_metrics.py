from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from intellicage import metrics as M


def test_mouse_metrics_against_hand_calculated_events(datasets):
    row = M.mouse_summary(datasets[0]).filter(animal=1).row(0, named=True)
    expected = {"visits_n": 3, "visits_duration_total_s": 60., "visits_duration_mean_s": 20.,
                "visits_with_nosepoke_n": 3, "visits_without_nosepoke_n": 0,
                "licks_n": 4, "licks_duration_total_s": 2., "licks_contact_time_total_s": 1.,
                "nosepokes_n": 4, "nosepokes_duration_total_s": 5., "nosepokes_duration_mean_s": 1.25,
                "nosepokes_with_licks_n": 1, "nosepokes_without_licks_n": 3,
                "nosepokes_correct_n": 2, "nosepokes_incorrect_n": 2,
                "licks_duration_per_visit_s": 2 / 3, "targeted_exploration_pct": 100.,
                "time_in_corners_total_s": 60., "nosepokes_correct_pct": 50.,
                "nosepokes_incorrect_pct": 50., "thirst_index": 2.}
    for key, value in expected.items():
        assert row[key] == pytest.approx(value), key
    assert (row["group"], row["lot"], row["phase"], row["animal"]) == ("control", "lot 1", "phase 1", 1)


def test_nonlearning_phase_has_no_learning_columns(datasets):
    ds = replace(datasets[0], learning=False)
    assert "nosepokes_correct_pct" not in M.mouse_summary(ds).columns
    assert "nosepokes_correct_n" not in M.mouse_summary(ds).columns
    assert M.learning_by_day(ds) is None


def test_corner_counts_complete_zero_corners_and_denominators(datasets):
    got = M.corner_summary(datasets[0]).filter(animal=1).sort("corner")
    assert got["visits_n"].to_list() == [2, 1, 0, 0]
    assert got["time_s"].to_list() == [40., 20., 0., 0.]
    assert got["visits_pct"].to_list() == pytest.approx([200 / 3, 100 / 3, 0, 0])
    assert got["time_pct"].sum() == pytest.approx(100)
    assert got["visit_duration_mean_s"].to_list() == [20., 20., None, None]


def test_learning_uses_phase_relative_days_and_scored_denominator(datasets):
    got = M.learning_by_day(datasets[0]).filter(animal=1).sort("day")
    assert got["day"].to_list() == [1, 2]
    assert got["correct_n"].to_list() == [1, 1]
    assert got["incorrect_n"].to_list() == [2, 0]
    assert got["incorrect_pct"].to_list() == pytest.approx([200 / 3, 0.])
    assert got["day_label"].to_list() == ["J1", "J2"]


@pytest.mark.parametrize("hours,min_hours,expected", [(48, 12, [1, 2]), (54, 12, [1, 2]),
                                                       (60, 12, [1, 2, 3]), (6, 12, [])])
def test_partial_day_threshold(datasets, hours, min_hours, expected):
    ds = replace(datasets[0], end=datasets[0].start + timedelta(hours=hours), sessions=None)
    assert M.complete_days(ds, min_hours) == expected


def test_hourly_counts_preserve_event_totals_and_zero_bins(datasets):
    ds = datasets[0]
    hourly = M.activity_hourly(ds)
    assert hourly["visits_n"].sum() == ds.visits.height
    assert hourly["nosepokes_n"].sum() == ds.nosepokes.height
    assert hourly["licks_n"].sum() == 12
    assert hourly.filter(pl.col("visits_n") == 0).height > 0
    assert hourly.filter(pl.col("clock_hour") == 8)["dark"].unique().to_list() == [False]
    assert hourly.filter(pl.col("clock_hour") == 22)["dark"].unique().to_list() == [True]
    inverted = M.activity_hourly(ds, light_on=19, light_off=7)
    assert inverted.filter(pl.col("clock_hour") == 22)["dark"].unique().to_list() == [False]
    assert M.activity_circadian(hourly).height == 3 * 24
    assert set(M.activity_light_dark(hourly)["period"]) == {"Light", "Dark"}


def test_latency_retains_unobserved_mice_with_censoring(datasets):
    got = M.latency(datasets[0])
    assert got.filter(animal=1)["latency_first_visit_s"][0] == 600
    assert got.filter(animal=1)["latency_corner_2_s"][0] == 4200
    short = M.latency(datasets[0], window_min=5)
    assert short["latency_first_visit_s"].null_count() == 3
    assert short["latency_first_visit_censored"].all()
    assert short["window_s"].unique().to_list() == [300]


def test_gate_pairs_and_summary_against_known_times(datasets):
    ds = datasets[0]
    sessions = M.gate_sessions(ds)
    assert sessions.height == 6
    assert set(sessions["directions"]) == {"In+Out"}
    got = M.gate_summary(ds, sessions, M.mouse_summary(ds)).filter(animal=1).row(0, named=True)
    assert got["gate_sessions_n"] == 1
    assert got["gate_duration_mean_s"] == 60
    assert got["gate_duration_total_s"] == 60
    assert got["weight_mean_g"] == 21
    assert got["gate_first_session_h"] == pytest.approx(3 / 60)
    assert got["visits_per_gate_session"] == 3


def test_absent_or_empty_gate_has_no_summary(datasets):
    ds = datasets[0]
    assert M.gate_sessions(replace(ds, gate_sessions=None)) is None
    assert M.gate_sessions(replace(ds, gate_sessions=ds.gate_sessions.head(0))) is None
    assert M.gate_summary(ds, None, M.mouse_summary(ds)) is None


def test_no_scored_nosepokes_gives_missing_percentage(datasets):
    ds = datasets[0]
    ds = replace(ds, nosepokes=ds.nosepokes.with_columns(pl.lit("Unscored").alias("SideCondition")))
    assert M.mouse_summary(ds)["nosepokes_correct_pct"].null_count() == 3
    assert M.learning_by_day(ds)["incorrect_pct"].null_count() == 6
