import io

import plotly.graph_objects as go
import pytest
from PIL import Image, PdfParser

from intellicage.dashboard_report import dashboard_pdf, filter_text, _font, _lines


def image_bytes():
    buffer = io.BytesIO()
    with Image.new('RGB', (100, 120), 'blue') as image: image.save(buffer, format='PNG')
    return buffer.getvalue()


def test_dashboard_pdf_has_all_selected_pages_in_order_and_keeps_source_figures(monkeypatch):
    renders = []
    def render(fig, **kwargs):
        renders.append((fig.layout.title.text, kwargs))
        assert fig.layout.font.weight == 700
        return image_bytes()
    monkeypatch.setattr(go.Figure, 'to_image', render)
    panels = [{'name': 'second', 'width': 480, 'height': 650}, {'name': 'first', 'width': 700, 'height': 450}]
    figures = {name: go.Figure(go.Bar(y=[1, 2])).update_layout(title=name).to_plotly_json() for name in ['first', 'second']}
    config = {'run_name': 'example', 'selection': {'groups': ['control', 'ko'], 'phases': ['phase 3']},
              'analyses': [{'name': name, 'table': 'mouse_summary', 'value': 'visits_n', 'section': 'Visits'} for name in figures]}
    geometry = {'width': 1196, 'cards': [
        {'name': 'second', 'x': 0, 'y': 180, 'width': 480, 'height': 650,
         'plot': {'x': 13, 'y': 241, 'width': 454, 'height': 576}},
        {'name': 'first', 'x': 496, 'y': 180, 'width': 700, 'height': 450,
         'plot': {'x': 509, 'y': 241, 'width': 674, 'height': 376}}],
        'bands': [{'kind': 'selection', 'x': 0, 'y': 0, 'width': 1196, 'height': 60},
                  {'kind': 'heading', 'text': 'overview · Visits', 'x': 0, 'y': 150, 'width': 1196, 'height': 24}]}
    config['report_mice'] = [{'group': 'control', 'lot': 'l', 'animal': 1}]
    data = dashboard_pdf(config, panels, figures, geometry)
    assert data.startswith(b'%PDF-')
    parsed = PdfParser.PdfParser(f=io.BytesIO(data))
    assert len(parsed.pages) == 1
    assert [title for title, _ in renders] == ['second', 'first']
    assert renders[0][1]['width'] == 454 and renders[0][1]['height'] == 576
    assert renders[1][1]['width'] == 674 and renders[1][1]['height'] == 376
    assert renders[0][1]['scale'] == 3
    assert figures['first']['layout']['title']['text'] == 'first'
    assert 'font' not in figures['first']['layout']


def test_pdf_validates_empty_or_unfinished_dashboard():
    with pytest.raises(ValueError, match='Choose graphs'): dashboard_pdf({}, [], {}, {})
    with pytest.raises(ValueError, match='finish loading'):
        dashboard_pdf({}, [{'name': 'missing'}], {}, {})


def test_pdf_renderer_failures_are_not_replaced_with_fake_graphs(monkeypatch):
    def fail(*args, **kwargs): raise RuntimeError('renderer failed')
    monkeypatch.setattr(go.Figure, 'to_image', fail)
    with pytest.raises(RuntimeError, match='renderer failed'):
        dashboard_pdf({}, [{'name': 'a', 'width': 480, 'height': 650}], {'a': go.Figure().to_plotly_json()},
            {'width': 480, 'bands': [], 'cards': [{'name': 'a', 'x': 0, 'y': 0, 'width': 480, 'height': 650,
              'plot': {'x': 13, 'y': 61, 'width': 454, 'height': 576}}]})


def test_report_fonts_and_wrapping_have_a_portable_fallback(monkeypatch):
    from PIL import ImageFont, ImageDraw
    def missing(*args, **kwargs): raise OSError('missing font')
    default = ImageFont.load_default(size=16)
    monkeypatch.setattr(ImageFont, 'truetype', missing)
    monkeypatch.setattr(ImageFont, 'load_default', lambda size: default)
    assert _font(16) is default
    with Image.new('RGB', (150, 200), 'white') as image:
        draw = ImageDraw.Draw(image)
        assert _lines(draw, "Welch&#x27;s t test<br>mean ± SEM", (0, 0), default, 80) > 40


def test_pdf_keeps_rows_whole_when_paginating(monkeypatch):
    monkeypatch.setattr(go.Figure, 'to_image', lambda *args, **kwargs: image_bytes())
    cards = [{'name': str(i), 'x': x, 'y': y, 'width': 480, 'height': 650,
              'plot': {'x': x + 13, 'y': y + 61, 'width': 454, 'height': 576}}
             for i, (x, y) in enumerate([(0, 0), (496, 0), (0, 680), (496, 680)])]
    panels = [{'name': c['name']} for c in cards]
    figures = {c['name']: go.Figure().to_plotly_json() for c in cards}
    data = dashboard_pdf({}, panels, figures, {'width': 976, 'cards': cards, 'bands': []})
    assert len(PdfParser.PdfParser(f=io.BytesIO(data)).pages) == 2
    with pytest.raises(ValueError, match='layout is not ready'): dashboard_pdf({}, panels, figures, {})
    with pytest.raises(ValueError, match='Invalid dashboard width'):
        dashboard_pdf({}, panels, figures, {'width': float('nan'), 'cards': cards})


def test_report_filters_include_selected_mice_and_dated_windows():
    config = {'selection': {'groups': ['KO'], 'phases': ['p3']}, 'report_mice': [{'animal': 7}],
              'settings': {'error': 'sd', 'show_ns': True, 'min_day_hours': 12, 'period_windows': [
                  {'name': 'day', 'start': '08:00', 'end': '20:00', 'ranges': [
                      {'date_from': '2026-10-01', 'date_to': '2026-10-05', 'start': '10:00', 'end': '16:00'}]},
                  {'name': 'night', 'start': '20:00', 'end': '08:00'}]}}
    assert 'groups: KO' in filter_text(config, 'selection')
    assert 'mice: 1' in filter_text(config, 'selection')
    assert 'error bars: SD' in filter_text(config, 'statistics')
    assert '2026-10-01–2026-10-05: 10:00–16:00' in filter_text(config, 'periods')
    assert 'night: 20:00–08:00' in filter_text(config, 'periods')
    assert 'minimum hours per day: 12' in filter_text(config, 'local')
