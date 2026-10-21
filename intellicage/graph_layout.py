from __future__ import annotations

import math
import re
import textwrap
from html import escape, unescape

import numpy as np
import plotly.graph_objects as go

KEY = 'intellicage_layout'


def wrap_text(text, pixels, size):
    plain = re.sub(r'<[^>]+>', '', re.sub(r'<br\s*/?>', '\n', str(text or ''), flags=re.I))
    width = max(8, int(pixels / (size * .58)))
    return '<br>'.join(escape(line) for paragraph in unescape(plain).split('\n')
                      for line in (textwrap.wrap(paragraph, width=width, break_long_words=True) or ['']))


def _axis_name(ref):
    return ref[0] + 'axis' + ref[1:]


def _data_limits(fig, ref):
    values = []
    for trace in fig.data:
        if (getattr(trace, 'yaxis', None) or 'y') != ref or trace.visible in (False, 'legendonly'):
            continue
        ys = getattr(trace, 'y', None)
        if ys is None: continue
        errors = getattr(getattr(trace, 'error_y', None), 'array', None)
        for i, value in enumerate(ys):
            if not isinstance(value, (int, float, np.number)) or not math.isfinite(value): continue
            error = errors[i] if errors is not None and i < len(errors) else 0
            error = float(error) if error is not None and math.isfinite(error) else 0
            values.extend([value - error, value + error])
    return min([0., *values]), max([1e-9, *values])


def _locked(patches, path):
    return any(key == path or key.startswith(path + '.') or path.startswith(key + '.') for key in patches)


def fit_figure(fig, width=None, height=None, patches=None):
    patches = patches or {}
    auto_height = height is None and fig.layout.width is None
    width, height = float(width or fig.layout.width or 900), float(height or fig.layout.height or 540)
    meta = dict(fig.layout.meta or {})
    state = dict(meta.get(KEY, {}))
    font = min(14, max(10, width / 43))
    title_size = fig.layout.title.font.size or 18
    if not _locked(patches, 'title.font.size'): title_size = min(18, max(13, width / 30))
    current = fig.layout.title.text or ''
    title = state.get('title_source', current) if current == state.get('title_wrapped') else current
    title_wrapped = wrap_text(title, width - 48, title_size)
    state.update(title_source=title, title_wrapped=title_wrapped)
    updates = {'title.text': title_wrapped, 'title.font.size': title_size,
               'title.x': .5, 'title.xanchor': 'center', 'title.y': .985, 'title.yanchor': 'top',
               'title.yref': 'container', 'title.automargin': False, 'title.font.weight': 600, 'font.size': font}
    title_height = (title_wrapped.count('<br>') + 1) * title_size * 1.3
    left, right, top = max(60, min(85, width * .12)), 22, max(48, title_height + 24)
    visible = [t for t in fig.data if t.showlegend is not False and t.visible is not False]
    legend_rows = 0
    if visible and fig.layout.showlegend is not False:
        entry_width = max(90, min(180, (width - left - right) / 2))
        sources = state.setdefault('legend_sources', {})
        line_count = 1
        for i, trace in enumerate(fig.data):
            if trace.showlegend is False or trace.visible is False: continue
            previous = sources.get(str(i), {})
            source = previous.get('source', trace.name) if trace.name == previous.get('wrapped') else trace.name
            wrapped = wrap_text(source, entry_width - 38, font)
            sources[str(i)] = {'source': source, 'wrapped': wrapped}
            trace.name = wrapped
            line_count = max(line_count, wrapped.count('<br>') + 1)
        legend_rows = max(1, math.ceil(len(visible) * entry_width / max(100, width - left - right))) * line_count
    tick_lines = max([1, *(str(label).count('<br>') + 1 for label in (fig.layout.xaxis.ticktext or []))])
    axis_space = max(52, tick_lines * font * 1.2 + 30)
    legend_space = legend_rows * (font * 1.5 + 7)
    footer_space = 0
    for index, note in enumerate(fig.layout.annotations):
        if note.name not in ('sample-size', 'analysis-summary'): continue
        sources = state.setdefault('footer_sources', {})
        previous = sources.get(note.name, {})
        source = previous.get('source', note.text) if note.text == previous.get('wrapped') else note.text
        wrapped = wrap_text(source, width - left - right, 10)
        sources[note.name] = {'source': source, 'wrapped': wrapped}
        values = {'text': wrapped, 'x': 0, 'xref': 'paper', 'y': 0, 'yref': 'paper', 'xanchor': 'left',
                  'yanchor': 'top', 'yshift': -(axis_space + legend_space + footer_space + 14),
                  'font.size': 10, 'align': 'left'}
        for key, value in values.items():
            if not _locked(patches, f'annotations[{index}].{key}'): note.update({key: value})
        footer_space += (wrapped.count('<br>') + 1) * 13 + 5
    bottom = axis_space + legend_space + footer_space + 24
    updates.update({'legend.orientation': 'h', 'legend.x': 0, 'legend.xanchor': 'left',
                    'legend.y': 0, 'legend.yanchor': 'top', 'legend.yref': 'paper',
                    'legend.font.size': font, 'legend.entrywidth': max(90, min(180, (width - left - right) / 2)),
                    'legend.entrywidthmode': 'pixels',
                    'margin.l': left, 'margin.r': right, 'margin.t': top, 'margin.b': bottom,
                    'legend.tracegroupgap': 0})
    for key, value in updates.items():
        if not _locked(patches, key): fig.update_layout({key: value})
    if auto_height:
        state.setdefault('base_height', height)
        height = max(state['base_height'], float(fig.layout.margin.t) + float(fig.layout.margin.b) + 240)
        fig.layout.height = height
    # pixel shifts keep footnotes away from axes at every aspect ratio
    plot_height = max(45, height - float(fig.layout.margin.t) - float(fig.layout.margin.b))
    if not _locked(patches, 'legend.y'):
        fig.layout.legend.y = -axis_space / plot_height
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    if not _locked(patches, 'xaxis.title.standoff'): fig.update_xaxes(title_standoff=12)
    if not _locked(patches, 'yaxis.title.standoff'): fig.update_yaxes(title_standoff=14)
    sources = state.setdefault('axis_sources', {})
    for name in fig.layout:
        if not re.fullmatch(r'[xy]axis[0-9]*', name): continue
        axis = fig.layout[name]
        if not axis.title.text: continue
        previous = sources.get(name, {})
        source = previous.get('source', axis.title.text) if axis.title.text == previous.get('wrapped') else axis.title.text
        pixels = width - left - right if name.startswith('x') else plot_height
        wrapped = wrap_text(source, max(90, pixels), axis.title.font.size or font)
        sources[name] = {'source': source, 'wrapped': wrapped}
        axis.title.text = wrapped
    marks = state.get('marks', [])
    lanes = {}
    for mark in marks:
        ref = mark['yref']
        axis = fig.layout[_axis_name(mark['xref'])]
        domain = axis.domain or (0, 1)
        span = max(50, (width - left - right) * (domain[1] - domain[0]))
        label_width = max(28, max(map(len, mark['text'].split('<br>'))) * 7)
        half = max(label_width / span / 2, mark.get('bracket_span', 0) / 2)
        interval = (mark['position'] - half, mark['position'] + half)
        rows = lanes.setdefault(ref, [])
        lane = next((i for i, ends in enumerate(rows) if all(interval[0] > end + .025 or interval[1] < start - .025 for start, end in ends)), len(rows))
        if lane == len(rows): rows.append([])
        rows[lane].append(interval)
        mark['lane'] = lane
    shapes = [s for s in fig.layout.shapes if s.name != 'stat-bracket']
    dense_panels = []
    for ref, rows in lanes.items():
        axis = fig.layout[_axis_name(ref)]
        domain = axis.domain or (0, 1)
        panel_height = max(35, plot_height * (domain[1] - domain[0]))
        panel_marks = [m for m in marks if m['yref'] == ref]
        series = list(dict.fromkeys(m['series'] for m in panel_marks))
        dense = len({m['position'] for m in panel_marks}) > max(8, (width - left - right) / 35)
        dense_panels.append(dense)
        count = len(series) if dense else len(rows)
        reserved = min(.7, (count * 36 + 16) / panel_height)
        low, high = _data_limits(fig, ref)
        if axis.autorange is not False and not _locked(patches, _axis_name(ref) + '.range'):
            axis.autorangeoptions.include = low + (high - low) / (1 - reserved)
        for mark in (m for m in marks if m['yref'] == ref):
            if mark['index'] >= len(fig.layout.annotations): continue
            note = fig.layout.annotations[mark['index']]
            lane = series.index(mark['series']) if dense else mark['lane']
            y = 1 - reserved + (lane + .5) * reserved / count
            changes = {'y': y, 'yref': ref + ' domain', 'yshift': 4, 'yanchor': 'bottom', 'font.size': 12}
            if dense:
                # every comparison remains available on hover
                stride = max(1, math.ceil(sum(m['yref'] == ref for m in marks) * 40 / max(100, width - left - right)))
                changes['text'] = mark['text'] if mark['order'] % stride == 0 else '·'
            else:
                changes['text'] = mark['text']
            for key, value in changes.items():
                if not _locked(patches, f"annotations[{mark['index']}].{key}"): note.update({key: value})
            if mark.get('x0') is not None and not dense:
                for x0, x1, y0, y1 in [(mark['x0'], mark['x0'], y - .02, y),
                                       (mark['x0'], mark['x1'], y, y), (mark['x1'], mark['x1'], y, y - .02)]:
                    shapes.append(go.layout.Shape(name='stat-bracket', type='line', xref=mark['xref'],
                        yref=ref + ' domain', x0=x0, x1=x1, y0=y0, y1=y1,
                        line={'color': '#222222', 'width': 1.4}))
    fig.layout.shapes = tuple(shapes)
    state['dense'] = any(dense_panels)
    meta[KEY] = state
    fig.layout.meta = meta
    return fig


def fit_plot(plot, width, height, patches=None):
    figure = plot.figure
    if not isinstance(figure, dict): return
    fig = go.Figure(data=figure.get('data', []), layout=figure.get('layout', {}))
    fit_figure(fig, width, height, patches)
    layout = fig.layout.to_plotly_json()
    layout.pop('width', None); layout.pop('height', None)
    layout['autosize'] = True
    figure['layout'] = layout
    figure['data'] = fig.to_plotly_json()['data']
    plot.update()
