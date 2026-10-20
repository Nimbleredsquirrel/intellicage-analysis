from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import polars as pl

from .stats import fmt_p

DEFAULT_COLORS = ['#2a78d6', '#eb6834', '#00cc96', '#ab63fa', '#ffa15a', '#19d3f3']
PLOT_CONFIG = {'editable': True, 'scrollZoom': False, 'displaylogo': False,
               'toImageButtonOptions': {'format': 'svg', 'filename': 'intellicage'},
               'edits': {'legendPosition': True, 'legendText': True, 'titleText': True,
                         'axisTitleText': True, 'annotationText': True, 'annotationPosition': True}}


def colors_for(groups: list[str], custom: dict | None = None) -> dict[str, str]:
    return {g: (custom or {}).get(g, DEFAULT_COLORS[i % len(DEFAULT_COLORS)]) for i, g in enumerate(groups)}


def _values(df, value):
    return df.filter(pl.col(value).is_finite())[value].cast(pl.Float64).to_numpy()


def _mean_error(values, error='sem'):
    if not len(values):
        return None, None
    mean = float(np.mean(values))
    sd = float(np.std(values, ddof=1)) if len(values) > 1 else 0.
    return mean, sd / np.sqrt(len(values)) if error == 'sem' else sd


def _meta(group, role, period=''):
    return {'group': group, 'role': role, 'period': period}


def _mouse_data(df):
    return df.select('animal', 'lot', 'phase').rows()


def _finish(fig, title, ylabel, xlabel='', ylim=None):
    fig.update_layout(template='simple_white', title={'text': title, 'x': .02, 'xanchor': 'left'},
                      font={'family': 'Arial, sans-serif', 'size': 14, 'color': '#222222'}, height=540,
                      margin={'l': 75, 'r': 30, 't': 85, 'b': 95}, hovermode='closest',
                      legend={'orientation': 'h', 'y': -0.22, 'x': 0},
                      xaxis_title=xlabel, yaxis_title=ylabel, paper_bgcolor='white', plot_bgcolor='white')
    fig.update_xaxes(showline=True, linewidth=1.5, ticks='outside', zeroline=False)
    fig.update_yaxes(showline=True, linewidth=1.5, ticks='outside', zeroline=False, rangemode='tozero')
    if ylim:
        fig.update_yaxes(range=ylim, autorange=False)
    return fig


def _test_note(fig, tests):
    if tests is None or not tests.height:
        return
    rows = [r for r in tests.to_dicts() if r.get('group_a') != 'all']
    text = ' · '.join(f"{r['group_a']} vs {r['group_b']}: {fmt_p(r.get('p_adj', r.get('p')))}" for r in rows)
    if text:
        fig.add_annotation(text=text, xref='paper', yref='paper', x=.5, y=1.1,
                           showarrow=False, font={'size': 11})


def column_plot(df, value, groups, colors, *, title, ylabel, pairwise=None, style='bar', ylim=None, error='sem'):
    fig = go.Figure()
    for i, group in enumerate(groups):
        sub = df.filter(pl.col('group') == group, pl.col(value).is_finite())
        values = _values(sub, value)
        mean, e = _mean_error(values, error)
        err = {'type': 'data', 'array': [e], 'color': '#222222', 'thickness': 1.5, 'width': 9}
        if style == 'bar':
            fig.add_bar(x=[i], y=[mean], width=.58, error_y=err, name=group, legendgroup=group,
                        marker={'color': colors[group], 'opacity': .35, 'line': {'color': colors[group], 'width': 2}},
                        meta=_meta(group, 'mean'), hovertemplate=f'{group}<br>mean %{{y:.5g}}<br>n={len(values)}<extra></extra>')
        else:
            fig.add_scatter(x=[i], y=[mean], mode='markers', name=group, legendgroup=group, error_y=err,
                            marker={'symbol': 'line-ew', 'size': 36, 'color': colors[group], 'line': {'width': 3}},
                            meta=_meta(group, 'mean'), hovertemplate=f'{group}<br>mean %{{y:.5g}}<extra></extra>')
        jitter = np.random.default_rng(i).uniform(-.13, .13, len(values))
        fig.add_scatter(x=(i + jitter).tolist(), y=values.tolist(), mode='markers', name=f'{group} · mice',
                        legendgroup=group, showlegend=False, meta=_meta(group, 'mice'),
                        marker={'color': colors[group], 'size': 9, 'line': {'color': '#222222', 'width': .8}},
                        customdata=_mouse_data(sub),
                        hovertemplate='mouse %{customdata[0]} · %{customdata[1]}<br>%{y:.5g}<extra></extra>')
    fig.update_xaxes(tickmode='array', tickvals=list(range(len(groups))), ticktext=groups)
    _test_note(fig, pairwise)
    return _finish(fig, title, ylabel, ylim=ylim)


def grouped_plot(df, value, category, groups, colors, *, title, ylabel, xlabel='', category_labels=None,
                 posthoc=None, ylim=None, error='sem'):
    fig = go.Figure()
    categories = sorted(df[category].unique().drop_nulls().to_list())
    labels = [(category_labels or {}).get(c, str(c)) for c in categories]
    for group in groups:
        means, errors, counts = [], [], []
        for category_value in categories:
            values = _values(df.filter(pl.col('group') == group, pl.col(category) == category_value), value)
            m, e = _mean_error(values, error)
            means.append(m); errors.append(e); counts.append(len(values))
        fig.add_bar(x=labels, y=means, name=group, legendgroup=group, offsetgroup=group,
                    meta=_meta(group, 'mean'), customdata=counts,
                    marker={'color': colors[group], 'opacity': .35, 'line': {'color': colors[group], 'width': 2}},
                    error_y={'type': 'data', 'array': errors, 'color': '#222222'},
                    hovertemplate=f'{group}<br>%{{x}}<br>mean %{{y:.5g}} · n=%{{customdata}}<extra></extra>')
        sub = df.filter(pl.col('group') == group, pl.col(value).is_finite())
        fig.add_scatter(x=[(category_labels or {}).get(c, str(c)) for c in sub[category]], y=sub[value].to_list(),
                        mode='markers', name=f'{group} · mice', showlegend=False, legendgroup=group, offsetgroup=group,
                        marker={'color': colors[group], 'size': 7, 'line': {'color': '#222222', 'width': .6}},
                        meta=_meta(group, 'mice'), customdata=_mouse_data(sub),
                        hovertemplate='mouse %{customdata[0]} · %{customdata[1]}<br>%{y:.5g}<extra></extra>')
    if posthoc is not None and posthoc.height and len(groups) == 2:
        for row in posthoc.to_dicts():
            category_value = next((c for c in categories if str(c) == row['level']), None)
            if category_value is not None:
                fig.add_annotation(x=(category_labels or {}).get(category_value, str(category_value)),
                                   y=1.04, yref='paper', showarrow=False, text=fmt_p(row.get('p_adj')),
                                   font={'size': 11})
    fig.update_layout(barmode='group', scattermode='group', scattergap=.5)
    return _finish(fig, title, ylabel, xlabel, ylim)


def timecourse_plot(df, value, x, groups, colors, *, title, ylabel, xlabel='', x_labels=None, posthoc=None,
                    dark_col=None, show_points=True, ylim=None, error='sem'):
    fig = go.Figure()
    xs = sorted(df[x].unique().drop_nulls().to_list())
    for group in groups:
        means, errors, counts = [], [], []
        for xv in xs:
            values = _values(df.filter(pl.col('group') == group, pl.col(x) == xv), value)
            mean, e = _mean_error(values, error)
            means.append(mean); errors.append(e); counts.append(len(values))
        fig.add_scatter(x=xs, y=means, mode='lines+markers' if len(xs) <= 30 else 'lines', name=group,
                        legendgroup=group, meta=_meta(group, 'mean'), customdata=counts,
                        line={'color': colors[group], 'width': 2.5},
                        error_y={'type': 'data', 'array': errors, 'color': colors[group], 'thickness': 1},
                        hovertemplate=f'{group}<br>%{{x}}<br>mean %{{y:.5g}} · n=%{{customdata}}<extra></extra>')
        if show_points:
            sub = df.filter(pl.col('group') == group, pl.col(value).is_finite())
            fig.add_scatter(x=sub[x].to_list(), y=sub[value].to_list(), mode='markers', name=f'{group} · mice',
                            legendgroup=group, visible='legendonly', meta=_meta(group, 'mice'),
                            marker={'color': colors[group], 'size': 6, 'opacity': .55}, customdata=_mouse_data(sub),
                            hovertemplate='mouse %{customdata[0]} · %{customdata[1]}<br>%{x}: %{y:.5g}<extra></extra>')
    if dark_col and dark_col in df.columns:
        dark = df.group_by(x).agg(pl.col(dark_col).first()).filter(dark_col)[x].to_list()
        # keep adjacent dark hours in a single shaded region
        runs = []
        for xv in sorted(dark):
            if runs and xv == runs[-1][1] + 1:
                runs[-1][1] = xv
            else:
                runs.append([xv, xv])
        for start, end in runs:
            fig.add_vrect(x0=start - .5, x1=end + .5, line_width=0, fillcolor='#dfe7e3', opacity=.5, layer='below')
    if posthoc is not None and posthoc.height and len(groups) == 2:
        for row in posthoc.to_dicts():
            if row.get('summary') not in ('', 'ns', None):
                xv = next((v for v in xs if str(v) == row['level']), None)
                if xv is not None:
                    fig.add_annotation(x=xv, y=1.03, yref='paper', showarrow=False,
                                       text=f"{row['summary']}<br>{fmt_p(row.get('p_adj'))}", font={'size': 10})
    if x_labels:
        fig.update_xaxes(tickmode='array', tickvals=xs, ticktext=[x_labels.get(v, str(v)) for v in xs])
    elif len(xs) > 30:
        fig.update_xaxes(dtick=24)
    return _finish(fig, title, ylabel, xlabel, ylim)


def period_timecourse(df, value, groups, periods, colors, *, title, ylabel, posthoc=None, ylim=None, error='sem'):
    fig = go.Figure()
    days = sorted(df['day'].unique().to_list())
    for group in groups:
        for i, period in enumerate(periods):
            means, errors, counts = [], [], []
            for day in days:
                values = _values(df.filter(group=group, period=period, day=day), value)
                mean, e = _mean_error(values, error)
                means.append(mean); errors.append(e); counts.append(len(values))
            fig.add_scatter(x=days, y=means, mode='lines+markers', name=f'{group} · {period}',
                            legendgroup=f'{group}|{period}', meta=_meta(group, 'mean', period), customdata=counts,
                            line={'color': colors[group], 'width': 2.5, 'dash': 'solid' if i % 2 == 0 else 'dash'},
                            marker={'symbol': 'circle' if i % 2 == 0 else 'diamond', 'size': 8},
                            error_y={'type': 'data', 'array': errors},
                            hovertemplate=f'{group} · {period}<br>cycle %{{x}}<br>%{{y:.5g}} · n=%{{customdata}}<extra></extra>')
    fig.update_xaxes(tickmode='array', tickvals=days, ticktext=[f'J{d}' for d in days])
    return _finish(fig, title, ylabel, 'Day/night cycle', ylim)


def latency_plot(df, value, groups, colors, *, title, ylabel='Latency (s)', pairwise=None, ylim=None):
    fig = go.Figure()
    for i, group in enumerate(groups):
        for censored in (False, True):
            sub = df.filter(pl.col('group') == group, pl.col(value).is_null() == censored)
            values = sub['window_s'].to_list() if censored else sub[value].to_list()
            jitter = np.random.default_rng(i + int(censored) * 100).uniform(-.13, .13, len(values))
            fig.add_scatter(x=(i + jitter).tolist(), y=values, mode='markers',
                            name=f'{group} · no visit in window' if censored else group, legendgroup=group,
                            meta=_meta(group, 'censored' if censored else 'observed'),
                            marker={'color': colors[group], 'size': 10,
                                    'symbol': 'triangle-up-open' if censored else 'circle'}, customdata=_mouse_data(sub),
                            hovertemplate=('mouse %{customdata[0]} · %{customdata[1]}<br>no visit within %{y:.0f} s<extra></extra>'
                                           if censored else 'mouse %{customdata[0]} · %{customdata[1]}<br>%{y:.3f} s<extra></extra>'))
    fig.update_xaxes(tickmode='array', tickvals=list(range(len(groups))), ticktext=groups)
    _test_note(fig, pairwise)
    return _finish(fig, title, ylabel, ylim=ylim)


def gate_raster(sessions, phase_starts, groups, colors, *, title):
    fig = go.Figure()
    labels = []
    for group in groups:
        for lot, animal in sessions.filter(group=group).select('lot', 'animal').unique().sort('lot', 'animal').iter_rows():
            labels.append(f'{animal} ({group}, {lot})')
        xs, ys, tips = [], [], []
        for row in sessions.filter(group=group).iter_rows(named=True):
            t0 = phase_starts[(group, row['lot'], row['phase'])]
            start = (row['start'] - t0).total_seconds() / 3600
            end = (row['end'] - t0).total_seconds() / 3600
            label = f"{row['animal']} ({group}, {row['lot']})"
            tip = f"mouse {row['animal']} · {row['start']}<br>{row['duration_s']:.3f} s · {row['directions']}"
            xs.extend([start, max(end, start + .01), None]); ys.extend([label, label, None]); tips.extend([tip, tip, None])
        fig.add_scatter(x=xs, y=ys, text=tips, mode='lines', name=group, line={'color': colors[group], 'width': 8},
                        meta=_meta(group, 'sessions'), hovertemplate='%{text}<extra></extra>')
    fig.update_yaxes(categoryorder='array', categoryarray=labels[::-1])
    _finish(fig, title, 'Mouse', 'Time from phase start (h)')
    fig.update_layout(height=max(440, 170 + len(labels) * 25))
    return fig


def save(fig: go.Figure, out_html: Path, out_png: Path | None = None):
    fig.write_html(out_html, include_plotlyjs='directory', config=PLOT_CONFIG)
    if out_png is not None:
        fig.write_image(out_png, width=1200, height=650, scale=2)
