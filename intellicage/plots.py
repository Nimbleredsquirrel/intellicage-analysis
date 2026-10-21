from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import polars as pl

from .stats import fmt_p, mean_interval

DEFAULT_COLORS = ['#2a78d6', '#eb6834', '#00cc96', '#ab63fa', '#ffa15a', '#19d3f3']
PNG_SCALE = 4
PLOT_CONFIG = {'editable': False, 'scrollZoom': False, 'displaylogo': False,
               'toImageButtonOptions': {'format': 'svg', 'filename': 'intellicage', 'width': None, 'height': None, 'scale': 1},
               'edits': {'legendPosition': False, 'legendText': False, 'titleText': False,
                         'axisTitleText': False, 'annotationText': False, 'annotationPosition': False}}


def colors_for(groups: list[str], custom: dict | None = None) -> dict[str, str]:
    return {g: (custom or {}).get(g, DEFAULT_COLORS[i % len(DEFAULT_COLORS)]) for i, g in enumerate(groups)}


def _values(df, value):
    return df.filter(pl.col(value).is_finite())[value].cast(pl.Float64).to_numpy()


def _mean_error(values, error='sem'):
    if not len(values):
        return None, None
    mean = float(np.mean(values))
    if len(values) < 2:
        return mean, None
    sd = float(np.std(values, ddof=1))
    if error == 'ci95':
        lo, hi = mean_interval(values)
        return mean, (hi - lo) / 2
    return mean, sd / np.sqrt(len(values)) if error == 'sem' else sd


def _meta(group, role, period=''):
    return {'group': group, 'role': role, 'period': period}


def _mouse_data(df):
    return df.select('animal', 'lot', 'phase').rows()


def _finish(fig, title, ylabel, xlabel='', ylim=None, *, size=(1000, 560), plot_height=260):
    from .graph_layout import KEY, fit_figure

    fig.update_layout(template='simple_white', title={'text': title, 'x': .02, 'xanchor': 'left'},
                      font={'family': 'Arial, sans-serif', 'size': 14, 'color': '#000000'}, height=size[1],
                      margin={'l': 75, 'r': 30, 't': 85, 'b': 95}, hovermode='closest',
                      legend={'orientation': 'h', 'y': -0.22, 'x': 0},
                      xaxis_title=xlabel, yaxis_title=ylabel, paper_bgcolor='white', plot_bgcolor='white')
    fig.update_xaxes(showline=True, linewidth=1.5, ticks='outside', zeroline=False)
    fig.update_yaxes(showline=True, linewidth=1.5, ticks='outside', zeroline=False, rangemode='tozero')
    if ylim:
        fig.update_yaxes(range=ylim, autorange=False)
    fig.layout.meta = {KEY: {'default_width': size[0], 'plot_height': plot_height, 'side_legend': bool(xlabel)}}
    return fit_figure(fig)


def _test_note(fig, tests):
    if tests is None or not tests.height:
        return
    rows = [r for r in tests.to_dicts() if r.get('group_a') != 'all']
    text = ' · '.join(f"{r['group_a']} vs {r['group_b']}: {fmt_p(r.get('p_adj', r.get('p')))}" for r in rows)
    if text:
        fig.add_annotation(name='stat', text=text, xref='paper', yref='paper', x=.5, y=1.1,
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
    fig.update_layout(showlegend=False)
    _test_note(fig, pairwise)
    size = (480, 680) if len(groups) <= 3 else (max(640, len(groups) * 120), 640)
    return _finish(fig, title, ylabel, ylim=ylim, size=size, plot_height=360)


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
                fig.add_annotation(name='stat', x=(category_labels or {}).get(category_value, str(category_value)),
                                   y=1.04, yref='paper', showarrow=False, text=fmt_p(row.get('p_adj')),
                                   font={'size': 11})
    fig.update_layout(barmode='group', scattermode='group', scattergap=.5)
    return _finish(fig, title, ylabel, xlabel, ylim, size=(max(760, len(categories) * 150), 600), plot_height=300)


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
                    fig.add_annotation(name='stat', x=xv, y=1.03, yref='paper', showarrow=False,
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
    fig.update_layout(showlegend=df[value].null_count() > 0)
    _test_note(fig, pairwise)
    return _finish(fig, title, ylabel, ylim=ylim, size=(480, 680), plot_height=360)


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
    fig.update_layout(height=max(540, 170 + len(labels) * 25))
    from .graph_layout import KEY
    fig.layout.meta[KEY]['base_height'] = fig.layout.height
    return fig


def save(fig: go.Figure, out_html: Path, out_png: Path | None = None):
    from .graph_layout import KEY
    fig = go.Figure(fig)
    if fig.layout.width is None:
        fig.layout.width = (fig.layout.meta or {}).get(KEY, {}).get('default_width', 900)
    fig.write_html(out_html, include_plotlyjs='directory', config=PLOT_CONFIG)
    if out_png is not None:
        fig.write_image(out_png, width=fig.layout.width or 900, height=fig.layout.height or 540, scale=PNG_SCALE)


def paired_period_plot(pairs, groups, colors, *, title, ylabel, error='sem', ylim=None, show_n=True):
    from plotly.subplots import make_subplots

    days = sorted(pairs['day'].unique().to_list())
    if not days:
        fig = go.Figure()
        fig.add_annotation(text='No complete mouse pairs for this selection', showarrow=False)
        return _finish(fig, title, ylabel, ylim=ylim)
    columns = min(3, len(days))
    rows = (len(days) + columns - 1) // columns
    fig = make_subplots(rows=rows, cols=columns, subplot_titles=[f'Cycle {d}' for d in days],
                        vertical_spacing=min(.16, .6 / rows))
    for index, day in enumerate(days):
        row, col = index // columns + 1, index % columns + 1
        labels, ticks = [], []
        for gi, group in enumerate(groups):
            sub = pairs.filter(group=group, day=day)
            # each segment belongs to one mouse in one cycle
            xs, ys, tips = [], [], []
            for mouse in sub.iter_rows(named=True):
                xs.extend([gi * 3, gi * 3 + 1, None])
                ys.extend([mouse['value_a'], mouse['value_b'], None])
                tip = f"mouse {mouse['animal']} · {mouse['lot']} · {mouse['phase']}"
                tips.extend([tip, tip, None])
            fig.add_trace(go.Scatter(x=xs, y=ys, text=tips, mode='lines+markers', name=group,
                legendgroup=group, showlegend=index == 0, meta=_meta(group, 'paired mice'),
                line={'color': colors[group], 'width': 1}, marker={'size': 6}, opacity=.55,
                hovertemplate='%{text}<br>%{y:.5g}<extra></extra>'), row=row, col=col)
            means, errors = zip(*[_mean_error(sub[field].to_numpy(), error) for field in ('value_a', 'value_b')])
            fig.add_trace(go.Scatter(x=[gi * 3, gi * 3 + 1], y=means, mode='markers',
                marker={'symbol': 'line-ew', 'size': 25, 'color': colors[group], 'line': {'width': 3}},
                error_y={'type': 'data', 'array': errors}, name=f'{group} · mean', showlegend=False,
                meta=_meta(group, 'paired mean'), customdata=[sub.height] * 2,
                hovertemplate='mean %{y:.5g}<br>paired n=%{customdata}<extra></extra>'), row=row, col=col)
            ticks.extend([gi * 3, gi * 3 + 1])
            names = [sub['period_a'][0], sub['period_b'][0]] if sub.height else ['day', 'night']
            labels.extend([f'{group}<br>{name}' + (f'<br>n={sub.height}' if show_n else '') for name in names])
        fig.update_xaxes(tickmode='array', tickvals=ticks, ticktext=labels, row=row, col=col)
    _finish(fig, title, ylabel, ylim=ylim)
    fig.update_layout(height=max(540, rows * 360 + 150))
    from .graph_layout import KEY
    fig.layout.meta[KEY]['base_height'] = fig.layout.height
    return fig
