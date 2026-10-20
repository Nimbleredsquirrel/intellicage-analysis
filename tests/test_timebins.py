from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from intellicage.timebins import (clock_minutes, hour_bins, observation_intervals,
                                 observed_hours, period_bins, period_windows)
from intellicage import metrics as M


@pytest.mark.parametrize('value,expected', [('10:30', 630), ('24:00', 0), (22, 1320), (4.5, 270)])
def test_clock_times(value, expected):
    assert clock_minutes(value) == expected


@pytest.mark.parametrize('value', ['25:00', '10:99', '-1:00', '24:01', 'x', None, 3.001, -1])
def test_invalid_clock_times(value):
    with pytest.raises(ValueError, match='Invalid clock time'):
        clock_minutes(value)


@pytest.mark.parametrize('windows,match', [([], 'at least one'),
    ([{'name':'x','start':7,'end':7}], 'different'),
    ([{'name':'','start':7,'end':8}], 'unique'),
    ([{'name':'x','start':7,'end':9},{'name':'x','start':10,'end':11}], 'unique'),
    ([{'name':'x','start':22,'end':4},{'name':'y','start':3,'end':8}], 'overlap')])
def test_invalid_windows(windows, match):
    with pytest.raises(ValueError, match=match): period_windows({'period_windows': windows})


def test_independent_windows_allow_gaps_and_wrap_midnight(datasets):
    settings = {'period_windows': [{'name':'day','start':'10:00','end':'16:00'},
                                   {'name':'night','start':'22:00','end':'04:00'}]}
    bins = period_bins(datasets[0], settings)
    assert bins.height == 4
    assert bins['hours'].to_list() == [6., 6., 6., 6.]
    assert bins['complete'].all()
    assert bins['day'].to_list() == [1,1,2,2]
    events = M.activity_periods(datasets[0], settings)
    assert events['visits_n'].sum() == 0
    assert events['visits_n_per_h'].sum() == 0
    assert events['visits_duration_mean_s'].null_count() == events.height


def test_observation_gaps_and_overlapping_sessions_are_not_double_counted(datasets):
    ds = datasets[0]
    start = ds.start
    sessions = pl.DataFrame({'start':[start, start + timedelta(minutes=30), start + timedelta(hours=2)],
                             'end':[start + timedelta(hours=1), start + timedelta(hours=1.5), start + timedelta(hours=3)]})
    ds = replace(ds, sessions=sessions, end=start + timedelta(hours=3))
    intervals = observation_intervals(ds)
    assert len(intervals) == 2
    assert observed_hours(intervals, start, ds.end) == 2.5
    bins = hour_bins(ds, align='phase')
    assert bins['hours'].to_list() == [1., .5, 1.]
    assert hour_bins(replace(ds,sessions=None),align='phase')['hours'].sum() == 3


def test_terminal_hour_is_not_an_extra_zero_bin(datasets):
    ds = replace(datasets[0], end=datasets[0].start+timedelta(hours=2))
    assert hour_bins(ds, align='phase').height == 2
    assert hour_bins(ds)['hours'].sum() == 2


def test_long_gate_session_is_wholly_in_its_starting_hour(datasets):
    ds = datasets[0]
    sessions = M.gate_sessions(ds).head(1).with_columns(duration_s=pl.lit(7500.))
    result = M.gate_hourly(ds, sessions)
    assert result['gate_duration_total_s'].sum() == 7500
    assert result.filter(pl.col('hour') == 0)['gate_duration_total_s'].sum() == 7500
    assert result['gate_sessions_n'].sum() == 1
    assert result.filter(pl.col('hour') > 0)['gate_duration_total_s'].sum() == 0


def test_partial_window_and_weighted_rates(datasets):
    ds = datasets[0]
    periods = M.activity_periods(ds, {})
    assert periods.filter(~pl.col('complete')).height > 0
    full = M.activity_light_dark(periods)
    all_windows = M.activity_light_dark(periods, include_partial=True)
    assert full['hours'].sum() < all_windows['hours'].sum()
    for row in all_windows.iter_rows(named=True):
        assert row['visits_n_per_h'] == row['visits_n'] / row['hours']
    clock = M.activity_circadian(M.activity_hourly(ds))
    row = clock.filter(animal=1,clock_hour=7).row(0,named=True)
    assert row['visits_n_per_h'] == row['visits_n_total'] / row['hours']


def test_learning_day_requires_recorded_hours_not_just_calendar_time(datasets):
    ds=datasets[0]
    sessions=pl.DataFrame({'start':[ds.start],'end':[ds.start+timedelta(hours=3)]})
    assert M.complete_days(replace(ds,sessions=sessions),12)==[]
    assert M.complete_days(replace(ds,sessions=sessions),2)==[1]


def test_latency_censors_at_recording_end_or_first_gap(datasets):
    ds=datasets[0]
    sessions=pl.DataFrame({'start':[ds.start,ds.start+timedelta(minutes=20)],
                          'end':[ds.start+timedelta(minutes=5),ds.end]})
    frame=M.latency(replace(ds,sessions=sessions))
    assert frame['latency_first_visit_censored'].all()
    assert frame['window_s'].unique().to_list()==[300.]


def test_recording_gaps_make_a_day_night_window_partial(datasets):
    ds=datasets[0]
    sessions=pl.DataFrame({'start':[ds.start],'end':[ds.start+timedelta(hours=1)]})
    frame=period_bins(replace(ds,sessions=sessions),{})
    assert not frame['complete'].any()
