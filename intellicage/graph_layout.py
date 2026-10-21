from __future__ import annotations

import math
import re
import textwrap
from html import escape, unescape

import numpy as np
import plotly.graph_objects as go

KEY = 'intellicage_layout'


def plain_text(text):
    text = re.sub(r'<br\s*/?>', '\n', str(text or ''), flags=re.I)
    return unescape(re.sub(r'</?[a-zA-Z][^>]*>', '', text))


def plot_text(text):
    return '<br>'.join(escape(line, quote=False) for line in plain_text(text).splitlines())


def wrap_text(text, pixels, size):
    plain = plain_text(text)
    width = max(8, int(pixels / (size * .58)))
    return '<br>'.join(escape(line, quote=False) for paragraph in plain.split('\n')
                      for line in (textwrap.wrap(paragraph, width=width, break_long_words=True) or ['']))


def wrap_caption(text, pixels, size):
    wrapped = wrap_text(text, pixels, size)
    return re.sub(r'P(?:=|&lt;)\d+(?:\.\d+)?',
                  lambda match: f'<span style="color:#000000"><b>{match[0]}</b></span>', wrapped)


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
    meta = dict(fig.layout.meta or {})
    state = dict(meta.get(KEY, {}))
    width = float(width or fig.layout.width or state.get('default_width', 900))
    height = float(height or fig.layout.height or 540)
    font = 14
    title_size = fig.layout.title.font.size or 18
    if not _locked(patches, 'title.font.size'): title_size = 18
    current = fig.layout.title.text or ''
    title = state.get('title_source', current) if current == state.get('title_wrapped') else current
    title_wrapped = wrap_text(title, width - 48, title_size)
    state.update(title_source=title, title_wrapped=title_wrapped)
    updates = {'title.text': title_wrapped, 'title.font.size': title_size,
               'title.x': .5, 'title.xanchor': 'center', 'title.y': .985, 'title.yanchor': 'top',
               'title.yref': 'container', 'title.automargin': False, 'title.font.weight': 700, 'font.size': font, 'font.weight': 700, 'font.color': '#000000', 'hoverlabel.font.size': font, 'hoverlabel.font.weight': 700}
    title_height = (title_wrapped.count('<br>') + 1) * title_size * 1.3
    left, right, top = max(60, min(85, width * .12)), 22, max(48, title_height + 24)
    side_legend = state.get('side_legend', False) and fig.layout.showlegend is not False
    if side_legend: right = max(130, min(220, width * .2))
    visible = [t for t in fig.data if t.showlegend is not False and t.visible is not False]
    legend_rows = 0
    if visible and fig.layout.showlegend is not False:
        entry_width = right - 12 if side_legend else max(90, min(180, (width - left - right) / 2))
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
    legend_space = 0 if side_legend else legend_rows * (font * 1.5 + 7)
    marks = state.get('marks', [])
    strip_space = 0
    strip_dense = False
    for heading in state.get('strip_headings', []):
        selected = [m for m in marks if m.get('placement') == 'strip' and m['series'] == heading['series']]
        if not selected: continue
        span = max(100, width - left - right)
        label_widths = {m['index']: max(map(len, m['text'].split('<br>'))) * font * .65 + 16 for m in selected}
        dense = sum(label_widths.values()) > span
        all_ns = dense and all(m['text'] == 'ns' for m in selected)
        heading_text = heading['text'] + (f' · ns for all {len(selected)} comparisons' if all_ns else '')
        text = wrap_text(heading_text, span, font)
        note = fig.layout.annotations[heading['index']]
        values = {'text': text, 'x': 0, 'xref': 'paper', 'y': 0, 'yref': 'paper', 'xanchor': 'left',
                  'yanchor': 'top', 'yshift': -(axis_space + legend_space + strip_space + 12),
                  'font.size': font, 'font.weight': 700}
        for key, value in values.items():
            if not _locked(patches, f"annotations[{heading['index']}].{key}"): note.update({key: value})
        title_height_strip = (text.count('<br>') + 1) * 18
        strip_dense = strip_dense or dense
        occupied, shown = [], set()
        # give significant labels room before adding ns labels
        for mark in sorted(selected, key=lambda m: (m['text'].startswith('ns'), m['position'])):
            half = label_widths[mark['index']] / 2
            interval = (mark['position'] * span - half, mark['position'] * span + half)
            if not all_ns and (not dense or all(interval[0] > end or interval[1] < start for start, end in occupied)):
                shown.add(mark['index'])
                occupied.append(interval)
        for mark in selected:
            label = fig.layout.annotations[mark['index']]
            values = {'text': mark['text'], 'visible': mark['index'] in shown, 'y': 0, 'yref': 'paper',
                'yshift': -(axis_space + legend_space + strip_space + 12 + title_height_strip),
                'xanchor': 'center', 'yanchor': 'top', 'font.size': font, 'font.weight': 700}
            for key, value in values.items():
                if not _locked(patches, f"annotations[{mark['index']}].{key}"): label.update({key: value})
        label_height = max((m['text'].count('<br>') + 1 for m in selected if m['index'] in shown), default=0) * 18
        strip_space += title_height_strip + label_height + 12
    footer_space = strip_space
    footer_order = {'analysis-heading': 0, 'analysis-summary': 1, 'sample-size': 2}
    footers = sorted(((i, n) for i, n in enumerate(fig.layout.annotations) if n.name in footer_order),
                     key=lambda item: footer_order[item[1].name])
    for index, note in footers:
        footer_font = 16 if note.name != 'sample-size' else font
        sources = state.setdefault('footer_sources', {})
        previous = sources.get(note.name, {})
        source = previous.get('source', note.text) if note.text == previous.get('wrapped') else note.text
        if note.name == 'analysis-heading' and not _locked(patches, f'annotations[{index}].text'):
            source = title
        wrap = wrap_caption if note.name == 'analysis-summary' else wrap_text
        wrapped = wrap(source, width - 32, footer_font)
        sources[note.name] = {'source': source, 'wrapped': wrapped}
        values = {'text': wrapped, 'x': 0, 'xref': 'paper', 'y': 0, 'yref': 'paper', 'xanchor': 'left',
                  'xshift': 16 - float(patches.get('margin.l', left)),
                  'yanchor': 'top', 'yshift': -(axis_space + legend_space + footer_space + 14),
                  'font.size': footer_font, 'font.weight': 700 if note.name == 'analysis-heading' else 400,
                  'font.color': '#000000' if note.name == 'analysis-heading' else '#555555', 'align': 'left'}
        for key, value in values.items():
            if not _locked(patches, f'annotations[{index}].{key}'): note.update({key: value})
        footer_space += (wrapped.count('<br>') + 1) * footer_font * 1.4 + 8
    bottom = axis_space + legend_space + footer_space + 24
    updates.update({'legend.orientation': 'h', 'legend.x': 0, 'legend.xanchor': 'left',
                    'legend.y': 0, 'legend.yanchor': 'top', 'legend.yref': 'paper',
                    'legend.font.size': font, 'legend.font.weight': 700, 'legend.entrywidth': max(90, min(180, (width - left - right) / 2)),
                    'legend.entrywidthmode': 'pixels',
                    'margin.l': left, 'margin.r': right, 'margin.t': top, 'margin.b': bottom,
                    'legend.tracegroupgap': 0})
    if side_legend:
        updates.update({'legend.orientation': 'v', 'legend.x': 1.02, 'legend.y': 1,
                        'legend.entrywidth': right - 12})
    for key, value in updates.items():
        if not _locked(patches, key): fig.update_layout({key: value})
    if auto_height:
        state.setdefault('base_height', height)
        height = max(state['base_height'], float(fig.layout.margin.t) + float(fig.layout.margin.b) + state.get('plot_height', 240))
        fig.layout.height = height
    # pixel shifts keep footnotes away from axes at every aspect ratio
    plot_height = max(45, height - float(fig.layout.margin.t) - float(fig.layout.margin.b))
    if not side_legend and not _locked(patches, 'legend.y'):
        fig.layout.legend.y = -axis_space / plot_height
    fig.update_xaxes(automargin=True, tickfont={'weight': 700}, title_font={'weight': 700})
    fig.update_yaxes(automargin=True, tickfont={'weight': 700}, title_font={'weight': 700})
    for note in fig.layout.annotations:
        if note.name not in ('analysis-summary', 'sample-size'):
            note.font.weight = 700
        if note.name == 'stat': note.font.color = '#000000'
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
    marks = [m for m in marks if m.get('placement') != 'strip']
    lanes = {}
    for mark in marks:
        ref = mark['yref']
        axis = fig.layout[_axis_name(mark['xref'])]
        domain = axis.domain or (0, 1)
        span = max(50, (width - left - right) * (domain[1] - domain[0]))
        label_width = max(28, max(map(len, mark['text'].split('<br>'))) * font * .65)
        half = max(label_width / span / 2, mark.get('bracket_span', 0) / 2)
        interval = (mark['position'] - half, mark['position'] + half)
        rows = lanes.setdefault(ref, [])
        lane = next((i for i, ends in enumerate(rows) if all(interval[0] > end + .025 or interval[1] < start - .025 for start, end in ends)), len(rows))
        if lane == len(rows): rows.append([])
        rows[lane].append(interval)
        mark['lane'] = lane
    for ref, included in state.get('axis_includes', {}).items():
        axis = fig.layout[_axis_name(ref)]
        if axis.autorangeoptions.include == included: axis.autorangeoptions.include = None
    includes = {}
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
            includes[ref] = axis.autorangeoptions.include
        for mark in (m for m in marks if m['yref'] == ref):
            if mark['index'] >= len(fig.layout.annotations): continue
            note = fig.layout.annotations[mark['index']]
            lane = series.index(mark['series']) if dense else mark['lane']
            y = 1 - reserved + (lane + .5) * reserved / count
            mark_font = 18 if set(mark['text']) == {'*'} else font
            changes = {'y': y, 'yref': ref + ' domain', 'yshift': 4, 'yanchor': 'bottom', 'visible': True,
                       'xanchor': 'center', 'font.size': mark_font, 'font.weight': 700}
            if dense:
                # every comparison remains available on hover
                stride = max(1, math.ceil(sum(m['yref'] == ref for m in marks) * 40 / max(100, width - left - right)))
                changes['text'] = mark['text']
                changes['visible'] = mark['order'] % stride == 0
            else:
                changes['text'] = mark['text']
            for key, value in changes.items():
                if not _locked(patches, f"annotations[{mark['index']}].{key}"): note.update({key: value})
            if mark.get('x0') is not None and not dense:
                cap = 7 / panel_height
                for x0, x1, y0, y1 in [(mark['x0'], mark['x0'], y - cap, y),
                                       (mark['x0'], mark['x1'], y, y), (mark['x1'], mark['x1'], y, y - cap)]:
                    shapes.append(go.layout.Shape(name='stat-bracket', type='line', xref=mark['xref'],
                        yref=ref + ' domain', x0=x0, x1=x1, y0=y0, y1=y1,
                        line={'color': '#000000', 'width': 3}, opacity=1, layer='above'))
    fig.layout.shapes = tuple(shapes)
    state['axis_includes'] = includes
    state['dense'] = strip_dense or any(dense_panels)
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
