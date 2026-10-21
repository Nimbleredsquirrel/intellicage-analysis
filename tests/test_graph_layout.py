import copy
import math

import numpy as np
import plotly.graph_objects as go
import polars as pl
import pytest

from intellicage import plots as P
from intellicage.graph_layout import KEY, _data_limits, fit_figure, fit_plot, wrap_text
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
    assert bracket[1].x0 == 0 and bracket[1].x1 == 1
    assert 0 < bracket[1].y0 < 1
    assert mark.yref == 'y domain'
    assert mark.y > 0
    assert fig.layout.yaxis.autorangeoptions.include > _data_limits(fig, 'y')[1]
    summary = next(n for n in fig.layout.annotations if n.name == 'analysis-summary')
    assert 'Welch' in summary.text and 'P=' in summary.text and 'SEM' in summary.text


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
    assert 'model P values' in text
    assert 'group: P=' in text and 'corner: P=' in text
    assert 'pairwise:' in text
    assert summary.yref == 'paper' and summary.y == 0 and summary.yshift < 0


def test_resizing_reflows_title_footer_and_preserves_source_and_dimensions(tables):
    title = 'Long title with lots of words for a meaningful comparison between the two experimental mouse groups'
    fig = result_for({'table': 'mouse_summary', 'value': 'visits_n', 'title': title}, tables)['figure']
    before = list(fig.data[0].y)
    fit_figure(fig, 420, 560)
    narrow = fig.layout.title.text
    assert '<br>' in narrow
    assert fig.layout.meta[KEY]['title_source'] == title
    fit_figure(fig, 1100, 560)
    assert fig.layout.title.text.count('<br>') < narrow.count('<br>')
    assert list(fig.data[0].y) == before
    # fitting does not replace the user's figure dimensions
    assert fig.layout.width is None and fig.layout.height == 540
    fig.update_layout(width=700, height=450)
    fit_figure(fig)
    assert (fig.layout.width, fig.layout.height) == (700, 450)
    offsets = [n.yshift for n in fig.layout.annotations if n.name in ('sample-size', 'analysis-summary')]
    assert offsets[1] < offsets[0]
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


def test_dense_hourly_marks_keep_all_hover_results_without_text_collisions():
    rng = np.random.default_rng(8)
    data = pl.DataFrame([{'group': g, 'lot': g, 'phase': 'p', 'animal': animal, 'hour': hour,
                         'y': 10 + rng.normal() + hour / 10}
                        for g in ['control', 'ko'] for animal in range(4) for hour in range(48)])
    a = {'table': 'activity_elapsed', 'value': 'y', 'by': 'hour'}
    fig = result_for(a, {'activity_elapsed': data}, repeated_test='lmm')['figure']
    fit_figure(fig, 480, 600)
    marks = [n for n in fig.layout.annotations if n.name == 'stat']
    assert len(marks) == 48
    assert any(n.text == '·' for n in marks)
    assert all(n.hovertext for n in marks)
    assert fig.layout.meta[KEY]['dense']
    fit_figure(fig, 2400, 700)
    assert all(n.text != '·' for n in fig.layout.annotations if n.name == 'stat')


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
