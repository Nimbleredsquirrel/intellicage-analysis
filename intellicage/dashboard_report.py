from __future__ import annotations

import io
import math
from itertools import groupby

import plotly.graph_objects as go
from PIL import Image, ImageDraw, ImageFont

from .graph_layout import fit_figure, plain_text


def _font(size):
    for path in ['/System/Library/Fonts/Supplemental/Arial Bold.ttf', 'DejaVuSans-Bold.ttf', 'arialbd.ttf']:
        try: return ImageFont.truetype(path, size)
        except OSError: pass
    return ImageFont.load_default(size=size)


def _lines(draw, text, position, font, width):
    x, y = position
    for paragraph in plain_text(text).splitlines():
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if line and draw.textlength(candidate, font=font) > width:
                draw.text((x, y), line, font=font, fill='black')
                y += font.size * 1.4
                line = word
            else: line = candidate
        draw.text((x, y), line, font=font, fill='black')
        y += font.size * 1.4
    return y


def filter_text(config, kind):
    selection, settings = config.get('selection', {}), config.get('settings', {})
    if kind == 'selection':
        groups = ', '.join(selection.get('groups', config.get('groups', [])))
        phases = ', '.join(selection.get('phases', []))
        return f'groups: {groups} · phase: {phases} · mice: {len(config.get("report_mice", []))}'
    if kind == 'statistics':
        labels = {'error': 'error bars', 'bar_style': 'summary style', 'test': 'group test',
                  'repeated_test': 'repeated measures', 'padjust': 'multiple comparisons',
                  'family_scope': 'comparison family', 'significance': 'statistical labels',
                  'show_ns': 'non-significant results', 'show_n': 'number of mice', 'show_test_summary': 'test summary'}
        values = {'sem': 'SEM', 'sd': 'SD', 'ci95': '95% CI', 'stars': 'Prism stars',
                  'p': 'p-value', 'both': 'stars + p-value', 'graph': 'this graph',
                  'section': 'this section', 'all': 'all configured graphs'}
        def value(v): return ('yes' if v else 'no') if isinstance(v, bool) else values.get(v, str(v))
        return ' · '.join(f'{label}: {value(settings[key])}' for key, label in labels.items() if key in settings)
    if kind == 'periods':
        lines = []
        from .timebins import period_windows
        def clock(minutes): return f'{minutes // 60:02}:{minutes % 60:02}'
        for window in period_windows(settings):
            lines.append(f"{window['name']}: {clock(window['start'])}–{clock(window['end'])}")
            lines.extend(f"{rule['date_from']}–{rule['date_to']}: {clock(rule['start'])}–{clock(rule['end'])}" for rule in window.get('ranges', []))
        lines.append('include incomplete first / last windows: ' + ('yes' if settings.get('include_partial_periods', False) else 'no'))
        return '\n'.join(lines)
    labels = {'latency_window_min': 'observation window · minutes', 'min_day_hours': 'minimum hours per day',
              'gate_complete_only': 'paired gate records only'}
    return ' · '.join(f'{label}: {settings[key] if settings[key] is not None else "full phase"}'
                      for key, label in labels.items() if key in settings)


def dashboard_pdf(config, panels, figures, geometry):
    if not panels: raise ValueError('Choose graphs before exporting the dashboard')
    if any(p['name'] not in figures for p in panels):
        raise ValueError('Wait for all dashboard graphs to finish loading')
    cards = geometry.get('cards', []) if isinstance(geometry, dict) else []
    if {c['name'] for c in cards} != {p['name'] for p in panels}:
        raise ValueError('Dashboard layout is not ready')
    width = float(geometry['width'])
    if not math.isfinite(width) or not 100 <= width <= 12000:
        raise ValueError('Invalid dashboard width')
    # keep dashboard rows together, including their preceding filters
    ordered = sorted(cards, key=lambda c: (round(c['y']), c['x']))
    rows = []
    for card in ordered:
        if rows and abs(rows[-1][0]['y'] - card['y']) < 4: rows[-1].append(card)
        else: rows.append([card])
    bands = sorted(geometry.get('bands', []), key=lambda b: b['y'])
    blocks, previous = [], 0
    for row in rows:
        top, bottom = min(c['y'] for c in row), max(c['y'] + c['height'] for c in row)
        labels = [b for b in bands if previous <= b['y'] < top]
        start = min([top, *(b['y'] for b in labels)])
        blocks.append({'top': start, 'bottom': bottom, 'cards': row, 'bands': labels})
        previous = bottom
    scale, margin = 2, 20
    height = max(1120, math.ceil(max(b['bottom'] - b['top'] for b in blocks)) + 190)
    output = io.BytesIO()
    pages = 0
    def new_page():
        page = Image.new('RGB', (math.ceil((width + 2 * margin) * scale), height * scale), 'white')
        draw = ImageDraw.Draw(page)
        y = _lines(draw, config.get('run_name', 'intellicage analysis'), (margin * scale, 20), _font(36), int(width * scale))
        mice = config.get('report_mice', [])
        records = []
        for key, entries in groupby(mice, key=lambda m: (m['group'], m['lot'])):
            records.append(f'{key[0]} / {key[1]}: ' + ', '.join(str(m['animal']) for m in entries))
        text = filter_text(config, 'selection')
        if records: text += '\nmice: ' + ' · '.join(records)
        y = _lines(draw, text, (margin * scale, y + 10), _font(26), int(width * scale))
        return page, draw, math.ceil(y / scale + 16)
    def save(page):
        nonlocal pages
        output.seek(0)
        page.save(output, format='PDF', resolution=192, append=pages > 0, title='intellicage analysis · dashboard', quality=95, subsampling=0)
        pages += 1
        page.close()
    page, draw, cursor = new_page()
    page_used = False
    previous_bottom = None
    for block in blocks:
        span = block['bottom'] - block['top']
        gap = max(0, block['top'] - previous_bottom) if previous_bottom is not None else 0
        if cursor + gap + span > height - 35 and page_used:
            save(page)
            page, draw, cursor = new_page()
            gap = 0
        cursor += gap
        shift = cursor - block['top']
        for band in block['bands']:
            text = band.get('text') if band['kind'] == 'heading' else filter_text(config, band['kind'])
            x, y = (band['x'] + margin) * scale, (band['y'] + shift) * scale
            _lines(draw, text, (x + 12, y + 6), _font(30 if band['kind'] == 'heading' else 26), max(100, int(band['width'] * scale - 24)))
        for card in block['cards']:
            source = figures[card['name']]
            fig = go.Figure(data=source.get('data', []), layout=source.get('layout', {}))
            rect = card['plot']
            w, h = max(100, round(rect['width'])), max(100, round(rect['height']))
            fig.update_layout(width=w, height=h, autosize=False)
            fit_figure(fig, w, h, config.get('styles', {}).get(card['name'], {}).get('layout', {}))
            png = fig.to_image(format='png', width=w, height=h, scale=min(3, 4096 / max(w, h)))
            left, top = (card['x'] + margin) * scale, (card['y'] + shift) * scale
            draw.rounded_rectangle((left, top, left + card['width'] * scale, top + card['height'] * scale), radius=12, outline='#cccccc', width=2)
            title = plain_text(fig.layout.title.text).replace('\n', ' ')
            font = _font(26)
            while title and draw.textlength(title + '…', font=font) > card['width'] * scale - 36:
                title = title[:-1]
            draw.text((left + 18, top + 10), title, font=font, fill='black')
            with Image.open(io.BytesIO(png)) as source_image:
                chart = source_image.convert('RGB').resize((round(rect['width'] * scale), round(rect['height'] * scale)), Image.Resampling.LANCZOS)
            page.paste(chart, (round((rect['x'] + margin) * scale), round((rect['y'] + shift) * scale)))
            chart.close()
        cursor += span
        previous_bottom = block['bottom']
        page_used = True
    save(page)
    return output.getvalue()
