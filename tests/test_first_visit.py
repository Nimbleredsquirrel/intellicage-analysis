from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from intellicage import metrics as M
from intellicage.pipeline import DEFAULT_SETTINGS, validate_settings
from intellicage.project import Project
from intellicage.prism import analysis_csv


def test_full_phase_distinguishes_waiting_time_and_visit_duration(datasets):
    row = M.latency(datasets[0]).filter(animal=1).row(0, named=True)
    assert DEFAULT_SETTINGS['latency_window_min'] is None
    assert row['window_s'] == 48 * 3600
    assert row['latency_first_visit_s'] == 600
    assert row['first_visit_duration_s'] == 10
    assert row['latency_corner_2_s'] == 4200
    assert row['corner_2_duration_s'] == 20
    assert row['first_visit_end'] - row['first_visit_start'] == timedelta(seconds=10)
    assert row['corner_4_duration_s'] is None
    assert row['latency_corner_4_censored']


def test_window_selects_start_but_keeps_whole_first_visit(datasets):
    row = M.latency(datasets[0], window_min=10.05).filter(animal=1).row(0, named=True)
    assert row['first_visit_duration_s'] == 10
    assert row['first_visit_end'] > datasets[0].start + timedelta(minutes=10.05)
    assert row['corner_2_duration_s'] is None
    assert row['latency_corner_2_censored']
    # the right boundary is outside the observation window
    short = M.latency(datasets[0], window_min=10)
    assert short['latency_first_visit_censored'].all()
    assert short['first_visit_duration_s'].null_count() == 3


def test_first_visit_never_replaced_by_a_later_longer_visit(datasets):
    ds = datasets[0]
    row = M.latency(replace(ds, visits=ds.visits.reverse())).filter(animal=1).row(0, named=True)
    assert row['corner_1_duration_s'] == 10
    assert row['latency_corner_1_s'] == 600


def test_incomplete_first_visit_has_no_duration(datasets):
    ds = datasets[0]
    end = ds.start + timedelta(minutes=10, seconds=5)
    row = M.latency(replace(ds, end=end)).filter(animal=1).row(0, named=True)
    assert row['latency_first_visit_s'] == 600
    assert not row['latency_first_visit_censored']
    assert row['first_visit_duration_s'] is None


def test_no_continuous_recording_at_phase_start(datasets):
    ds = datasets[0]
    sessions = pl.DataFrame({'start': [ds.start + timedelta(minutes=1)], 'end': [ds.end]})
    table = M.latency(replace(ds, sessions=sessions))
    assert table['latency_first_visit_censored'].all()
    assert table['window_s'].unique().to_list() == [0.]
    assert table['followup_interrupted'].all()


@pytest.mark.parametrize('window', [None, 60, 2880])
def test_observation_window_allows_full_phase_and_more_than_one_day(window):
    validate_settings({**DEFAULT_SETTINGS, 'latency_window_min': window})


@pytest.mark.parametrize('window', [-1, 0, float('nan'), float('inf'), True])
def test_invalid_observation_window(window):
    with pytest.raises(ValueError, match='Latency window'):
        validate_settings({**DEFAULT_SETTINGS, 'latency_window_min': window})


def test_duration_export_and_project_restore_keep_both_metrics(config, tmp_path):
    config['analyses'] = 'all'
    project = Project(config)
    analyses = [a for a in project.analyses if a['section'] == 'First visit duration']
    assert len(analyses) == 5
    result = project.result(analyses[0]['name'])
    assert result['data']['first_visit_duration_s'].to_list() == [10.] * 6
    assert result['stats']['descriptive']['mean'].to_list() == [10., 10.]
    matrix = analysis_csv(analyses[0], project.tables['latency'], project.groups)
    assert matrix.content == '10.0,10.0\n10.0,10.0\n10.0,10.0\n'
    project.rebuild({'latency_window_min': 5})
    assert project.result(analyses[0]['name'])['data'].is_empty()
    matrix = analysis_csv(analyses[0], project.tables['latency'], project.groups)
    assert matrix.content == ',\n,\n,\n'
    reopened = Project.open(project.save(), tmp_path / 'restored')
    assert reopened.cfg['settings']['latency_window_min'] == 5
    reopened.rebuild({'latency_window_min': None})
    assert reopened.result(analyses[0]['name'])['data'].height == 6
