import json

import numpy as np
import polars as pl
import pytest
from scipy import stats as ss

from intellicage import plots as P, stats as S
from intellicage.catalogue import analysis_name
from intellicage.inference import compute_results, significance_label, sample_sizes
from intellicage.pipeline import DEFAULT_SETTINGS, render_analysis, run, validate_settings
from intellicage.project import Project


@pytest.mark.parametrize('p,expected', [(0, '****'), (.0001, '****'), (.001, '***'), (.01, '**'), (.05, '*')])
def test_prism_uses_inclusive_unrounded_boundaries(p, expected):
    assert S.stars(p) == expected
    if p:
        assert S.stars(np.nextafter(p, np.inf)) != expected


def test_significance_modes_do_not_reuse_rounded_or_stale_summary():
    assert significance_label(.010001, 'stars') == '*'
    assert significance_label(.01, 'stars') == '**'
    assert significance_label(.01, 'p') == 'P=0.0100'
    assert significance_label(.01, 'both') == '**<br>P=0.0100'
    assert significance_label(.7, 'stars') == 'ns'
    assert significance_label(.7, 'stars', False) == ''
    assert significance_label(.001, 'none') == ''
    assert significance_label(np.nan) == ''
    assert significance_label(None) == ''


def test_ci_mean_known_answer_and_unavailable_single_mouse():
    values = np.array([1., 2., 3., 4., 5.])
    margin = 2.7764451051977987 / np.sqrt(2)
    np.testing.assert_allclose(S.mean_interval(values), [3 - margin, 3 + margin])
    assert S.mean_interval([1, np.nan, np.inf]) == (None, None)
    assert P._mean_error([1], 'ci95') == (1, None)
    assert P._mean_error([], 'ci95') == (None, None)
    assert P._mean_error(values, 'ci95')[1] == pytest.approx(margin)
    table = pl.DataFrame({'n': [5, 1], 'mean': [3., 2.], 'sem': [1 / np.sqrt(2), None]})
    result = S.descriptive_intervals(table)
    assert result['ci95_low'][0] == pytest.approx(3 - margin)
    assert result['ci95_low'][1] is None
    assert 'ci95_low' in S.descriptive_intervals(table.clear()).columns


@pytest.mark.parametrize('test', ['welch', 'student'])
def test_difference_ci_agrees_with_scipy(test):
    a, b = np.array([1., 2., 4.]), np.array([3., 5., 8., 11.])
    data = pl.DataFrame({'group': ['a'] * len(a) + ['b'] * len(b), 'y': np.r_[a, b]})
    _, result = S.compare_groups(data, 'y', ['a', 'b'], test)
    expected = ss.ttest_ind(b, a, equal_var=test == 'student').confidence_interval()
    assert result['ci95_low'][0] == pytest.approx(expected.low)
    assert result['ci95_high'][0] == pytest.approx(expected.high)
    assert 'pointwise' in result['ci_method'][0]
    assert S.difference_interval(a, b, 'Mann-Whitney test')['ci95_low'] is None
    assert S.difference_interval(a, b, 'wilcoxon', paired=True)['ci95_low'] is None
    assert S.difference_interval(np.ones(3), np.ones(3), "Welch's t test")['ci95_low'] is None


def pairs_data():
    return pl.DataFrame([
        {'group': group, 'lot': lot, 'phase': 'p', 'animal': animal, 'day': day,
         'period': period, 'y': float(animal + offset + day)}
        for group in ['a', 'b'] for lot in ['one', 'two'] for animal in [1, 2, 3]
        for day in [1, 2] for period, offset in [('Light', 0), ('Dark', animal)]])


def test_pairs_join_full_mouse_identity_and_cycle_and_reject_duplicates():
    data = pairs_data()
    data = data.filter(~((pl.col('group') == 'a') & (pl.col('lot') == 'one') &
                         (pl.col('animal') == 1) & (pl.col('day') == 1) & (pl.col('period') == 'Dark')))
    pairs = S.paired_values(data, 'y', ['Light', 'Dark'])
    assert pairs.height == 23
    assert pairs.filter(group='a', lot='one', animal=1, day=1).is_empty()
    assert pairs['diff_b_minus_a'].to_list() == pairs['animal'].cast(pl.Float64).to_list()
    with pytest.raises(ValueError, match='More than one'):
        S.paired_values(pl.concat([data, data.head(1)]), 'y', ['Light', 'Dark'])
    assert S.paired_values(data, 'y', ['Light']).is_empty()
    tests = S.period_comparisons(data, 'y', ['a', 'b'], ['Light', 'Dark'])
    contrast = tests.filter(group_a='a', contrast='within mouse', day=1).row(0, named=True)
    assert contrast['n'] == 5
    difference = pairs.filter(group='a', day=1)['diff_b_minus_a'].to_numpy()
    expected = ss.ttest_1samp(difference, 0).confidence_interval()
    assert contrast['ci95_low'] == pytest.approx(expected.low)
    assert contrast['ci95_high'] == pytest.approx(expected.high)


def test_paired_view_segments_have_two_values_from_one_mouse_and_keeps_facet_titles():
    data = pairs_data()
    a = {'table': 'activity_periods', 'value': 'y', 'by': 'day', 'plot': 'period_timecourse'}
    settings = {**DEFAULT_SETTINGS, 'period_style': 'paired', 'error': 'ci95', 'significance': 'stars', 'show_ns': True}
    result = compute_results([a], {'activity_periods': data}, ['a', 'b'], P.colors_for(['a', 'b']), settings, {})[analysis_name(a)]
    fig = result['figure']
    for trace in fig.data:
        if trace.meta['role'] != 'paired mice': continue
        for i in range(0, len(trace.y), 3):
            assert trace.y[i + 2] is None
            assert trace.text[i] == trace.text[i + 1]
    assert len(result['stats']['paired_values']) == 24
    assert any(note.text == 'Cycle 1' for note in fig.layout.annotations)
    assert any(note.name == 'stat' for note in fig.layout.annotations)
    assert any('n=6' in label for label in fig.layout.xaxis.ticktext)
    assert result['stats']['paired_descriptive']['n'].to_list() == [6] * 8
    empty = P.paired_period_plot(result['stats']['paired_values'].clear(), ['a'], {'a': 'blue'}, title='empty', ylabel='y')
    assert 'No complete' in empty.layout.annotations[0].text


def test_missing_counts_include_absent_rows_and_do_not_count_visits():
    data = pairs_data().filter(~((pl.col('animal') == 1) & (pl.col('day') == 2)))
    data = data.with_columns(pl.when((pl.col('animal') == 2) & (pl.col('day') == 2)).then(None).otherwise('y').alias('y'))
    counts = sample_sizes(data, {'value': 'y', 'by': 'day', 'plot': 'period_timecourse'})
    assert counts.filter(day=2)['n'].to_list() == [2] * 4
    assert counts.filter(day=2)['n_total'].to_list() == [6] * 4
    assert counts.filter(day=2)['missing'].to_list() == [4] * 4
    duplicate = pl.concat([data, data])
    assert sample_sizes(duplicate, {'value': 'y', 'by': 'day', 'plot': 'period_timecourse'}).equals(counts)


def family_definitions():
    return [dict(name=name, table='mouse_summary', value=metric, title=name, section=section)
            for name, metric, section in [('visits', 'visits_n', 'Visits'), ('visits_duration', 'visits_duration_total_s', 'Visits'),
                                          ('nosepokes', 'nosepokes_n', 'Nosepokes')]]


@pytest.mark.parametrize('scope,sizes', [('graph', [1, 1, 1]), ('section', [2, 2, 1]), ('all', [3, 3, 3])])
def test_family_scope_changes_adjusted_p_and_preserves_raw_p(config, scope, sizes):
    config['analyses'] = family_definitions()
    config['settings'] = {'family_scope': scope, 'padjust': 'bonferroni', 'significance': 'both', 'show_ns': True}
    project = Project(config)
    for a, size in zip(project.analyses, sizes):
        result = project.result(analysis_name(a))
        row = result['stats']['test'].row(0, named=True)
        assert row['family_size'] == size
        assert row['family_scope'] == scope
        assert row['family_phase'] == 'phase 1'
        assert row['p_adj'] == pytest.approx(min(1, row['p'] * size))
        assert row['summary'] == S.stars(row['p_adj'])
        assert result['tests'][0]['p_adj'] == row['p_adj']
    before = project.result('visits')['stats']['test']
    project.cfg['workspace'] = {'charts': [{'name': 'visits'}]}
    assert project.result('visits')['stats']['test'].equals(before)
    project.set_selection({**project.selection, 'exclude_mice': [{'group': 'control', 'lot': 'lot 1', 'animal': 1}]})
    assert not project.cache
    assert project.result('visits')['stats']['descriptive'].filter(group='control')['n'][0] == 2


def test_raw_tukey_contrasts_are_not_adjusted_twice():
    data = pl.DataFrame({'group': ['a'] * 3 + ['b'] * 3 + ['c'] * 3, 'y': [1., 2., 3., 2., 3., 4., 3., 4., 5.]})
    _, results = S.compare_groups(data, 'y', ['a', 'b', 'c'], 'anova')
    row = results.filter(group_a='a', group_b='c').row(0, named=True)
    assert row['p'] == pytest.approx(2 * ss.t.sf(2 / np.sqrt(2 / 3), 6))
    assert row['p_adj'] >= row['p']


def test_batch_only_does_not_shrink_family_and_matches_saved_project(config, tmp_path):
    config['analyses'] = family_definitions()
    config['settings'] = {'family_scope': 'all', 'padjust': 'holm', 'error': 'ci95', 'significance': 'stars'}
    project = Project(config)
    expected = project.result('visits')['stats']['test']
    project.styles['visits'] = {'size': {'width': 750, 'height': 500}}
    path = tmp_path / 'saved.yaml'
    import yaml
    path.write_text(yaml.safe_dump(project.configuration()))
    out = run(path, only=['visits'])
    actual = pl.read_csv(out / 'stats/visits__test.csv')
    assert actual['family_size'][0] == 3
    np.testing.assert_allclose(actual['p_adj'], expected['p_adj'])
    figure = json.loads((out / 'figures/json/visits.json').read_text())
    assert figure['layout']['width'] == 750
    assert len(list((out / 'figures/json').glob('*.json'))) == 1
    restored = Project.open(project.save(), tmp_path / 'reopened')
    assert restored.cfg['settings']['significance'] == 'stars'
    assert restored.result('visits')['stats']['test'].equals(expected)


def test_missing_values_audit_survives_finite_plot_filter(tables):
    data = tables['mouse_summary'].with_columns(pl.when(pl.col('animal') == 1).then(None).otherwise('visits_n').alias('visits_n'))
    a = {'table': 'mouse_summary', 'value': 'visits_n'}
    result = render_analysis(a, data, ['control', 'ko'], P.colors_for(['control', 'ko']), DEFAULT_SETTINGS, {})
    assert result['stats']['sample_sizes']['missing'].to_list() == [1, 1]
    assert result['data'].height == 4
    assert all('n=2' in annotation.text for annotation in result['figure'].layout.annotations if annotation.name == 'sample-size')


@pytest.mark.parametrize('key,value', [('error', 'bad'), ('bar_style', 'bad'), ('significance', 'bad'),
    ('family_scope', 'bad'), ('period_style', 'bad'), ('show_n', 'yes'), ('show_ns', 1)])
def test_new_settings_are_validated(key, value):
    with pytest.raises(ValueError, match=key):
        validate_settings({**DEFAULT_SETTINGS, key: value})


def test_shared_family_preserves_omnibus_and_annotates_each_repeated_level(tables):
    a = {'table': 'corner_summary', 'value': 'time_s', 'by': 'corner'}
    settings = {**DEFAULT_SETTINGS, 'family_scope': 'all', 'show_ns': True, 'significance': 'both'}
    result = compute_results([a], tables, ['control', 'ko'], P.colors_for(['control', 'ko']), settings, {})[analysis_name(a)]
    finite = result['stats']['posthoc'].filter(pl.col('p_adj').is_finite()).height
    assert finite > 0
    assert len([n for n in result['figure'].layout.annotations if n.name == 'stat']) == finite
    assert all(n.x.startswith('Corner ') for n in result['figure'].layout.annotations if n.name == 'stat')
    assert result['stats']['posthoc']['family_size'].to_list() == [4] * 4
    assert 'family_id' not in result['stats']['omnibus'].columns
    hours = {'table': 'learning_by_day', 'value': 'incorrect_pct', 'by': 'day'}
    line = compute_results([hours], tables, ['control', 'ko'], P.colors_for(['control', 'ko']), settings, {})[analysis_name(hours)]
    assert all(isinstance(n.x, int) for n in line['figure'].layout.annotations if n.name == 'stat')


def test_empty_family_members_missing_tables_and_invalid_analysis(tables):
    absent = {'name': 'absent', 'table': 'missing', 'value': 'y'}
    empty = {'name': 'empty', 'table': 'mouse_summary', 'value': 'visits_n', 'filter': {'animal': 999}}
    results = compute_results([absent, empty], tables, ['control'], P.colors_for(['control']), DEFAULT_SETTINGS, {})
    assert list(results) == ['empty']
    assert results['empty']['data'].is_empty()
    invalid = {'table': 'mouse_summary', 'value': 'missing_metric'}
    with pytest.raises(ValueError, match='Missing columns'):
        compute_results([invalid], tables, ['control'], P.colors_for(['control']), DEFAULT_SETTINGS, {})


def test_multigroup_shared_family_uses_raw_p_and_leaves_omnibus_separate():
    data = pl.DataFrame({'group': ['a'] * 3 + ['b'] * 3 + ['c'] * 3, 'lot': ['l'] * 9,
        'phase': ['p'] * 9, 'animal': [1, 2, 3] * 3, 'y': [1., 2., 3., 2., 3., 4., 3., 4., 5.]})
    a = {'table': 'mouse_summary', 'value': 'y'}
    for scope in ['graph', 'all']:
        settings = {**DEFAULT_SETTINGS, 'family_scope': scope, 'test': 'anova', 'padjust': 'bonferroni', 'show_ns': True}
        result = compute_results([a], {'mouse_summary': data}, ['a', 'b', 'c'], P.colors_for(['a', 'b', 'c']), settings, {})[analysis_name(a)]
        tests = result['stats']['test']
        omnibus = tests.filter(group_a='all').row(0, named=True)
        assert omnibus['family_id'] is None
        assert omnibus['p'] == omnibus['p_adj']
        contrasts = tests.filter(pl.col('group_a') != 'all')
        assert contrasts['family_size'].to_list() == [3] * 3
        if scope == 'all':
            np.testing.assert_allclose(contrasts['p_adj'], np.minimum(1, contrasts['p'].to_numpy() * 3))
        else:
            assert contrasts['correction'].to_list() == ['tukey'] * 3


def test_paired_view_requires_exactly_two_windows():
    with pytest.raises(ValueError, match='exactly two'):
        validate_settings({**DEFAULT_SETTINGS, 'period_style': 'paired',
                           'period_windows': [{'name': 'day', 'start': 8, 'end': 20}]})
