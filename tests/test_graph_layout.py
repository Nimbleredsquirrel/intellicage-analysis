import copy
import math

import numpy as np
import plotly.graph_objects as go
import polars as pl
import pytest

from intellicage import plots as P
from intellicage import stats as S
from intellicage.graph_layout import (KEY, _data_limits, comparison_settings, fit_figure, fit_plot,
                                    plain_text, style_comparisons, wrap_caption, wrap_text)
from intellicage.inference import compute_results
from intellicage.pipeline import DEFAULT_SETTINGS
from intellicage.project import Project


def result_for(a, tables, **settings):
    return next(iter(compute_results([a], tables, ['control', 'ko'], P.colors_for(['control', 'ko']),
                                    {**DEFAULT_SETTINGS, **settings}, {}).values()))


def test_default_prism_bracket_has_ns_and_exact_p_on_hover(tables):
    result = result_for({'table': 'mouse_summary', 'value': 'visits_n'}, tables)
    fig = result['figure']
    mark = next(n for n in fig.layout.annotations if n.name == 'stat')
    p = result['stats']['test']['p_adj'][0]
    from intellicage.stats import stars
    assert mark.text == stars(p)
    assert 'adjusted P' in mark.hovertext and 'raw P' in mark.hovertext
    bracket = [s for s in fig.layout.shapes if s.name == 'stat-bracket']
    assert len(bracket) == 3
    assert all(s.line.width == 3 and s.line.color == '#000000' and s.opacity == 1 and s.layer == 'above' for s in bracket)
    assert bracket[1].x0 == 0 and bracket[1].x1 == 1
    assert 0 < bracket[1].y0 < 1
    assert mark.yref == 'y domain'
    assert mark.y > 0
    assert fig.layout.yaxis.autorangeoptions.include > _data_limits(fig, 'y')[1]
    summary = next(n for n in fig.layout.annotations if n.name == 'analysis-summary')
    assert 'Welch' in summary.text and '<b>P=' in summary.text
    assert 'ns' not in summary.text and 'SEM' not in summary.text


def test_caption_wrap_keeps_only_p_values_bold_and_escapes_labels():
    text = "• Control & KO: P<0.0001 (Welch's t test)"
    wrapped = wrap_caption(text, 260, 16)
    assert '<span style="color:#000000"><b>P&lt;0.0001</b></span>' in wrapped
    assert '&amp;' in wrapped and '&#x27;' not in wrapped
    assert plain_text(wrapped).replace('\n', ' ') == text
    assert wrap_caption(wrapped, 260, 16) == wrapped


@pytest.mark.parametrize('analysis,portrait', [
    ({'table': 'mouse_summary', 'value': 'visits_n'}, True),
    ({'table': 'corner_summary', 'value': 'time_s', 'by': 'corner'}, False),
    ({'table': 'learning_by_day', 'value': 'correct_pct', 'by': 'day'}, False),
])
def test_default_proportions_follow_the_kind_of_graph(config, analysis, portrait):
    config['analyses'] = [analysis]
    project = Project(config)
    name = next(iter(project.analyses))
    from intellicage.catalogue import analysis_name
    fig = project.result(analysis_name(name))['figure']
    assert (fig.layout.height > fig.layout.width) == portrait
    if portrait:
        assert (fig.layout.width, fig.layout.height) == (480, 680)
        assert not fig.layout.showlegend
    else:
        assert fig.layout.legend.orientation == 'v'
        assert fig.layout.legend.x > 1 and fig.layout.margin.r >= 130
    caption = next(n for n in fig.layout.annotations if n.name == 'analysis-summary')
    assert caption.font.weight == 400
    assert fig.layout.margin.l + caption.xshift == 16
    project.resize_graph(analysis_name(name), {'width': 800, 'height': 550})
    resized = project.result(analysis_name(name))['figure']
    assert (resized.layout.width, resized.layout.height) == (800, 550)


def test_ns_is_visible_only_for_estimable_non_significant_comparisons(tables):
    result = result_for({'table': 'corner_summary', 'value': 'time_s', 'by': 'corner'}, tables)
    stats = result['stats']['posthoc']
    marks = [n for n in result['figure'].layout.annotations if n.name == 'stat']
    assert len(marks) == stats.filter(pl.col('p_adj').is_finite()).height
    assert any(n.text == 'ns' for n in marks)
    assert len([s for s in result['figure'].layout.shapes if s.name == 'stat-bracket']) == 3 * len(marks)
    hidden = result_for({'table': 'corner_summary', 'value': 'time_s', 'by': 'corner'}, tables, show_ns=False)
    assert all(n.text != 'ns' for n in hidden['figure'].layout.annotations if n.name == 'stat')
    assert all(n.name != 'stat' for n in result_for({'table': 'mouse_summary', 'value': 'visits_n'}, tables,
                                                 significance='none')['figure'].layout.annotations)


def test_repeated_summary_separates_omnibus_from_pairwise(tables):
    result = result_for({'table': 'corner_summary', 'value': 'time_s', 'by': 'corner'}, tables)
    summary = next(n for n in result['figure'].layout.annotations if n.name == 'analysis-summary')
    text = summary.text
    assert 'mixed ANOVA' in text
    assert 'group:' in text and 'corner:' in text and '<b>P=' in text
    assert 'pairwise:' not in text
    assert summary.yref == 'paper' and summary.y == 0 and summary.yshift < 0


@pytest.mark.parametrize('mode', ['p', 'stars', 'both'])
def test_lower_caption_uses_pdf_heading_and_one_bullet_per_effect(tables, mode):
    from intellicage.graph_layout import plain_text

    fig = result_for({'table': 'corner_summary', 'value': 'time_s', 'by': 'corner',
                      'title': 'Time spent in each corner'}, tables, significance=mode, show_n=True)['figure']
    notes = {n.name: n for n in fig.layout.annotations if n.name in ('analysis-heading', 'analysis-summary', 'sample-size')}
    assert plain_text(notes['analysis-heading'].text) == 'Time spent in each corner'
    source = fig.layout.meta[KEY]['footer_sources']['analysis-summary']['source']
    bullets = [line for line in source.split('<br>') if line.startswith('• ')]
    assert len(bullets) == 3
    assert all(line.endswith('(Two-way mixed ANOVA)') for line in bullets)
    assert bullets[0].startswith('• group: ') and bullets[1].startswith('• corner: ')
    assert bullets[2].startswith('• Interaction: ')
    assert 'P=' in source or 'P<' in source
    assert notes['analysis-heading'].font.weight == 700
    assert notes['analysis-summary'].font.weight == 400
    assert '<b>P' in notes['analysis-summary'].text
    assert notes['analysis-summary'].font.size == 16
    assert notes['analysis-heading'].yshift > notes['analysis-summary'].yshift > notes['sample-size'].yshift
    fig.update_layout(title_text='Renamed corner comparison')
    fit_figure(fig, 480, 650)
    assert plain_text(notes['analysis-heading'].text).replace('\n', ' ') == 'Renamed corner comparison'


@pytest.mark.parametrize('width,height', [(480, 650), (900, 540), (1000, 900)])
def test_significance_brackets_have_fixed_pixel_caps_and_centered_large_stars(tables, width, height):
    data = tables['corner_summary'].with_columns(
        pl.when(pl.col('group') == 'ko').then(pl.col('time_s') + 1000).otherwise(pl.col('time_s')).alias('time_s'))
    fig = result_for({'table': 'corner_summary', 'value': 'time_s', 'by': 'corner'}, {'corner_summary': data})['figure']
    fit_figure(fig, width, height)
    stars = [n for n in fig.layout.annotations if n.name == 'stat' and set(n.text) == {'*'}]
    assert stars and all(n.font.size == 18 and n.xanchor == 'center' for n in stars)
    plot_height = max(45, height - fig.layout.margin.t - fig.layout.margin.b)
    caps = [s for s in fig.layout.shapes if s.name == 'stat-bracket' and s.x0 == s.x1]
    assert caps and all(abs(s.y1 - s.y0) * plot_height == pytest.approx(7) for s in caps)


def test_resizing_reflows_title_footer_and_preserves_source_and_dimensions(tables):
    title = 'Long title with lots of words for a meaningful comparison between the two experimental mouse groups'
    fig = result_for({'table': 'mouse_summary', 'value': 'visits_n', 'title': title}, tables, show_n=True)['figure']
    before = list(fig.data[0].y)
    fit_figure(fig, 420, 560)
    narrow = fig.layout.title.text
    assert '<br>' in narrow
    assert fig.layout.meta[KEY]['title_source'] == title
    fit_figure(fig, 1100, 560)
    assert fig.layout.title.text.count('<br>') < narrow.count('<br>')
    assert list(fig.data[0].y) == before
    # fitting does not replace the user's figure dimensions
    assert fig.layout.width is None and fig.layout.height >= 540
    fig.update_layout(width=700, height=450)
    fit_figure(fig)
    assert (fig.layout.width, fig.layout.height) == (700, 450)
    offsets = {n.name: n.yshift for n in fig.layout.annotations if n.name in ('sample-size', 'analysis-summary', 'analysis-heading')}
    assert offsets['sample-size'] < offsets['analysis-summary'] < offsets['analysis-heading']
    bottommost = max(-n.yshift + 13 * (n.text.count('<br>') + 1) for n in fig.layout.annotations if n.name in ('sample-size', 'analysis-summary'))
    assert fig.layout.margin.b > bottommost
    snapshot = fig.to_plotly_json()
    fit_figure(fig)
    assert fig.to_plotly_json() == snapshot


def test_manual_positions_fonts_and_axis_limits_survive_reflow(tables):
    fig = result_for({'table': 'mouse_summary', 'value': 'visits_n'}, tables)['figure']
    fig.update_layout(title_x=.1, title_font_size=24, legend_y=-.4, margin_l=100,
                      yaxis_range=[0, 25], yaxis_autorange=False, xaxis_title_standoff=35)
    patches = {'title.x': .1, 'title.font.size': 24, 'legend.y': -.4, 'margin.l': 100,
               'yaxis.range': [0, 25], 'xaxis.title.standoff': 35}
    fit_figure(fig, 700, 500, patches)
    assert fig.layout.title.x == .1 and fig.layout.title.font.size == 24
    assert fig.layout.legend.y == -.4 and fig.layout.margin.l == 100
    assert tuple(fig.layout.yaxis.range) == (0, 25)
    assert fig.layout.xaxis.title.standoff == 35


def test_overlapping_three_group_comparisons_use_different_lanes(tables):
    data = tables['mouse_summary']
    third = data.filter(group='ko').with_columns(pl.lit('third').alias('group'), pl.col('visits_n') + 3)
    data = pl.concat([data, third])
    a = {'table': 'mouse_summary', 'value': 'visits_n'}
    fig = next(iter(compute_results([a], {'mouse_summary': data}, ['control', 'ko', 'third'],
        P.colors_for(['control', 'ko', 'third']), DEFAULT_SETTINGS, {}).values()))['figure']
    marks = fig.layout.meta[KEY]['marks']
    assert len({m['lane'] for m in marks}) == 3
    assert len([s for s in fig.layout.shapes if s.name == 'stat-bracket']) == 9
    assert len({n.y for n in fig.layout.annotations if n.name == 'stat'}) == 3


def test_dense_hourly_marks_keep_all_hover_results_without_text_collisions(monkeypatch):
    rng = np.random.default_rng(8)
    data = pl.DataFrame([{'group': g, 'lot': g, 'phase': 'p', 'animal': animal, 'hour': hour,
                         'y': 10 + rng.normal() + hour / 10}
                        for g in ['control', 'ko'] for animal in range(4) for hour in range(48)])
    # layout checks use fixed results, not an optimizer
    omnibus = pl.DataFrame([{'metric': 'y', 'test': 'LMM', 'effect': 'group', 'p': .8, 'note': ''}],
                          schema=S.OMNIBUS_SCHEMA)
    posthoc = pl.DataFrame([{'level': str(hour), 'group_a': 'control', 'group_b': 'ko',
                            'test': "Welch's t test", 'p': .001 if hour % 12 == 0 else .8,
                            'p_adj': .048 if hour % 12 == 0 else 1.}
                           for hour in range(48)])

    def fixed_results(df, value, within, groups, test, posthoc_test, padjust):
        assert df.height == 384
        assert (value, within, groups, test) == ('y', 'hour', ['control', 'ko'], 'lmm')
        return omnibus, posthoc

    monkeypatch.setattr(S, 'repeated_measures', fixed_results)
    a = {'table': 'activity_elapsed', 'value': 'y', 'by': 'hour'}
    fig = result_for(a, {'activity_elapsed': data}, repeated_test='lmm')['figure']
    fit_figure(fig, 480, 600)
    marks = [n for n in fig.layout.annotations if n.name == 'stat']
    assert len(marks) == 48
    assert sum(n.text == '*' for n in marks) == 4
    assert sum(n.text == 'ns' for n in marks) == 44
    assert any(n.visible is False for n in marks)
    assert all(n.text != '·' for n in marks)
    assert all(n.hovertext for n in marks)
    for trace in fig.data:
        if trace.meta['role'] != 'mean': continue
        assert '%{hovertext}' in trace.hovertemplate
        assert len(trace.hovertext) == 48
        assert all(f'hour: {hour}' in text and 'adjusted P' in text for hour, text in enumerate(trace.hovertext))
    assert fig.layout.meta[KEY]['dense']
    fit_figure(fig, 2400, 700)
    assert all(n.visible is False for n in fig.layout.annotations if n.name == 'stat' and n.text == 'ns')
    style_comparisons(fig, {'display': 'all'})
    fit_figure(fig, 2400, 700)
    assert all(n.text != '·' and n.visible is True for n in fig.layout.annotations if n.name == 'stat')


def test_dense_ns_strip_is_one_summary_and_resizing_restores_labels():
    fig = go.Figure(go.Scatter(x=list(range(168)), y=[1] * 168))
    marks = []
    for hour in range(168):
        fig.add_annotation(name='stat', text='ns', x=hour, xref='x', showarrow=False, hovertext=f'hour {hour}: P=0.8')
        marks.append({'index': hour, 'placement': 'strip', 'series': 'control|KO', 'text': 'ns',
                      'position': (hour + .5) / 168, 'xref': 'x', 'yref': 'y'})
    fig.add_annotation(name='stat-heading', text='control vs KO', showarrow=False)
    fig.layout.meta = {KEY: {'marks': marks, 'strip_headings': [
        {'index': 168, 'series': 'control|KO', 'text': 'control vs KO'}]}}
    fit_figure(fig, 1200, 600)
    assert 'ns for all 168 comparisons' in plain_text(fig.layout.annotations[-1].text)
    assert all(n.visible is False for n in fig.layout.annotations if n.name == 'stat')
    assert all(n.hovertext for n in fig.layout.annotations if n.name == 'stat')
    style_comparisons(fig, {'display': 'significant'})
    fit_figure(fig, 1200, 600)
    assert all(n.visible is False for n in fig.layout.annotations)
    style_comparisons(fig, {'display': 'compact'})
    # mixed results keep significant labels and leave space between labels
    for index in (40, 42, 44): marks[index]['text'] = '**'
    fig.layout.meta[KEY]['marks'] = marks
    fit_figure(fig, 1200, 600)
    assert 'ns for 165 of 168 comparisons' in plain_text(fig.layout.annotations[-1].text)
    shown = [m for m in marks if fig.layout.annotations[m['index']].visible]
    span = 1200 - fig.layout.margin.l - fig.layout.margin.r
    positions = sorted(m['position'] * span for m in shown)
    assert any(m['text'] == '**' for m in shown)
    assert all(b - a > 2 * 14 * .65 + 16 for a, b in zip(positions, positions[1:]))
    assert all(n.text != '·' for n in fig.layout.annotations)
    fit_figure(fig, 8000, 600)
    assert all(n.visible is False for n in fig.layout.annotations if n.name == 'stat' and n.text == 'ns')
    style_comparisons(fig, {'display': 'all'})
    fit_figure(fig, 8000, 600)
    assert all(n.visible is True for n in fig.layout.annotations if n.name == 'stat')


@pytest.mark.parametrize('values', [{'display': 'bad'}, {'extra': 1}, {'font_size': None},
                                  {'font_size': True}, {'line_width': 0}, {'star_size': float('inf')}])
def test_comparison_appearance_rejects_invalid_values(values):
    with pytest.raises(ValueError): comparison_settings(values)


def test_compact_view_counts_all_day_night_contrasts_before_summarizing_ns():
    fig = go.Figure(go.Scatter(x=list(range(7)), y=[1] * 7))
    marks, headings = [], []
    for series in ['control day/night', 'KO day/night', 'day control/KO', 'night control/KO']:
        for day in range(7):
            index = len(fig.layout.annotations)
            fig.add_annotation(name='stat', text='ns', x=day, xref='x', showarrow=False)
            marks.append({'index': index, 'placement': 'strip', 'series': series, 'text': 'ns',
                          'position': (day + .5) / 7, 'xref': 'x', 'yref': 'y'})
    for series in dict.fromkeys(m['series'] for m in marks):
        headings.append({'index': len(fig.layout.annotations), 'series': series, 'text': series})
        fig.add_annotation(name='stat-heading', text=series, showarrow=False)
    fig.layout.meta = {KEY: {'marks': marks, 'strip_headings': headings}}
    fit_figure(fig, 1200, 700)
    assert all(n.visible is False for n in fig.layout.annotations if n.name == 'stat')
    assert sum('ns for all 7 comparisons' in plain_text(n.text) for n in fig.layout.annotations) == 4


def test_comparison_display_and_appearance_do_not_change_tests_or_cached_figure(config, tmp_path):
    project = Project(config)
    from intellicage.catalogue import analysis_name
    name = analysis_name(project.analyses[0])
    original = project.result(name)
    stats = original['stats']['test'].clone()
    project.edit_comparisons(name, {'display': 'none', 'font_size': 22, 'line_width': 5})
    hidden = project.result(name)['figure']
    assert all(n.visible is False for n in hidden.layout.annotations if n.name == 'stat')
    assert not any(s.name == 'stat-bracket' for s in hidden.layout.shapes)
    assert project.result(name)['stats']['test'].equals(stats)
    assert any(n.visible is not False for n in project.cache[name]['figure'].layout.annotations if n.name == 'stat')
    project.edit_comparisons(name, {'display': 'significant'})
    assert all(n.visible is False for n in project.result(name)['figure'].layout.annotations if n.name == 'stat')
    project.edit_comparisons(name, {'display': 'all', 'font_size': 22, 'line_width': 5})
    shown = project.result(name)['figure']
    assert all(n.visible and n.font.size == 22 for n in shown.layout.annotations if n.name == 'stat')
    assert all(s.line.width == 5 and s.line.color == '#000000' for s in shown.layout.shapes if s.name == 'stat-bracket')
    restored = Project.open(project.save(), tmp_path / 'comparison-style')
    assert restored.styles[name]['comparisons'] == project.styles[name]['comparisons']
    assert restored.result(name)['stats']['test'].equals(stats)


def test_wrapping_preserves_special_characters_and_respects_line_breaks():
    assert wrap_text('a < b & c', 200, 10) == 'a &lt; b &amp; c'
    assert wrap_text('one<br>two', 200, 10) == 'one<br>two'
    assert '<br>' in wrap_text('a' * 100, 100, 10)
    fig = go.Figure(go.Scatter(y=[-2., None, np.nan, 5.], error_y={'array': [1, 0, 0, 2]}))
    assert _data_limits(fig, 'y') == (-3., 7.)
    fig.data[0].visible = 'legendonly'
    assert _data_limits(fig, 'y') == (0., 1e-9)


def test_layout_callback_keeps_plot_responsive_without_replacing_data(tables):
    fig = result_for({'table': 'mouse_summary', 'value': 'visits_n'}, tables)['figure']
    class Plot:
        figure = fig.to_plotly_json()
        updates = 0
        def update(self): self.updates += 1
    plot = Plot()
    data = copy.deepcopy(plot.figure['data'])
    fit_plot(plot, 420, 500)
    assert plot.updates == 1
    assert plot.figure['layout']['autosize'] is True
    assert 'width' not in plot.figure['layout'] and 'height' not in plot.figure['layout']
    assert plot.figure['data'] == data
    plot.figure = go.Figure()
    fit_plot(plot, 500, 600)
    assert plot.updates == 1


@pytest.mark.parametrize('mode,expected', [('p', 'P='), ('stars', 'ns'), ('both', 'P=')])
def test_statistical_mode_changes_plot_marks_while_caption_keeps_exact_p(tables, mode, expected):
    fig = result_for({'table': 'mouse_summary', 'value': 'visits_n'}, tables, significance=mode)['figure']
    footer = next(a for a in fig.layout.annotations if a.name == 'analysis-summary')
    mark = next(a for a in fig.layout.annotations if a.name == 'stat')
    assert expected in mark.text
    assert '<b>P=' in footer.text
    assert '&#x27;' not in footer.text
    assert fig.layout.font.weight == 700
    assert fig.layout.legend.font.weight == 700
    assert fig.layout.xaxis.tickfont.weight == 700
    assert fig.layout.yaxis.title.font.weight == 700
    assert all(a.font.weight == 700 for a in fig.layout.annotations if a.name not in ('analysis-summary', 'sample-size'))
    assert footer.font.weight == 400
    assert footer.font.size >= 14


def test_annotation_editor_text_does_not_expose_html():
    from intellicage.graph_layout import plain_text, plot_text
    text = "Group 2 vs Default: P=0.0599 · Welch&#x27;s t test<br>mean ± SEM"
    plain = plain_text(text)
    assert plain == "Group 2 vs Default: P=0.0599 · Welch's t test\nmean ± SEM"
    assert '&#x27;' not in plot_text(plain)
    assert '<br>' in plot_text(plain)


def test_day_night_comparisons_are_below_axes_with_unambiguous_headings(tables):
    a = {'table': 'activity_periods', 'value': 'visits_n', 'by': 'day', 'plot': 'period_timecourse'}
    fig = result_for(a, tables)['figure']
    labels = [note for note in fig.layout.annotations if note.name == 'stat']
    headings = [note.text for note in fig.layout.annotations if note.name == 'stat-heading']
    assert all(note.yref == 'paper' and note.y == 0 and note.yshift < 0 for note in labels)
    assert all('day/night' not in note.text and 'control' not in note.text for note in labels)
    assert any('vs ko' in text for text in headings)
    assert any('control:' in text for text in headings)
