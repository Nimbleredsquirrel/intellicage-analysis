from __future__ import annotations

import os
import sys
from pathlib import Path


def chrome_worker():
    # kaleido reuses the app executable on macos
    bundle = getattr(sys, '_MEIPASS', None)
    if not getattr(sys, 'frozen', False) or sys.platform == 'win32' or not bundle or len(sys.argv) < 3:
        return False
    wrapper = Path(bundle) / 'choreographer/browsers/_unix_pipe_chromium_wrapper.py'
    if Path(sys.argv[1]).resolve() != wrapper.resolve():
        return False
    sys.argv = sys.argv[1:]
    for name, fd in [('stdout', 1), ('stderr', 2)]:
        if getattr(sys, name) is None:
            setattr(sys, name, os.fdopen(fd, 'w', buffering=1, closefd=False))
    # keep this import visible to pyinstaller
    import choreographer.browsers._unix_pipe_chromium_wrapper  # noqa: F401

    return True


def prepare_stdio():
    # windowed windows apps have no console streams
    for name, mode in [('stdin', 'r'), ('stdout', 'w'), ('stderr', 'w')]:
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode))


def error_message(error):
    from choreographer.errors import BrowserFailedError, ChromeNotFoundError

    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ChromeNotFoundError):
            return 'PNG, SVG and PDF export needs Google Chrome. Install Chrome, open it once, then restart intellicage analysis. HTML graphs and CSV tables are still available.'
        if isinstance(current, BrowserFailedError):
            return 'Chrome could not start for image export. Open Chrome, finish any update, then restart intellicage analysis. HTML graphs and CSV tables are still available.'
        current = current.__cause__ or current.__context__
    return str(error)


def check_exports(destination):
    import io
    import json
    import xml.etree.ElementTree as ET

    import plotly.graph_objects as go
    import polars as pl
    from PIL import Image, PdfParser

    from .dashboard_report import dashboard_pdf
    from .graph_layout import fit_figure
    from .plots import PNG_SCALE, save
    from .table_view import excel_bytes

    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == 'win32':
            from webview.platforms.winforms import renderer
            if renderer != 'edgechromium':
                raise RuntimeError('Microsoft Edge WebView2 Runtime is required for the desktop window')
        figure = go.Figure(go.Bar(x=['control', 'KO'], y=[2, 3]))
        figure.update_layout(title='export check · mice', width=480, height=680)
        fit_figure(figure, 480, 680)
        save(figure, folder / 'graph.html', folder / 'graph.png')
        with Image.open(folder / 'graph.png') as image:
            if image.size != (480 * PNG_SCALE, 680 * PNG_SCALE):
                raise RuntimeError('PNG size differs from the selected graph size')
        svg = figure.to_image(format='svg')
        ET.fromstring(svg)
        (folder / 'graph.svg').write_bytes(svg)
        geometry = {'width': 506, 'bands': [], 'cards': [
            {'name': 'graph', 'x': 0, 'y': 0, 'width': 506, 'height': 754,
             'plot': {'x': 13, 'y': 61, 'width': 480, 'height': 680}}]}
        pdf = dashboard_pdf({}, [{'name': 'graph'}], {'graph': figure.to_plotly_json()}, geometry)
        if len(PdfParser.PdfParser(f=io.BytesIO(pdf)).pages) != 1:
            raise RuntimeError('Dashboard PDF has an unexpected number of pages')
        (folder / 'dashboard.pdf').write_bytes(pdf)
        table = pl.DataFrame({'mouse': ['мышь 1', 'mouse 2'], 'visits': [2, 3]})
        table.write_csv(folder / 'table.csv')
        table.write_csv(folder / 'table.txt', separator='\t')
        (folder / 'table.xlsx').write_bytes(excel_bytes(table))
        check_statistics(folder)
        (folder / 'check.json').write_text(json.dumps({'ok': True}), encoding='utf8')
    except Exception:
        import traceback
        (folder / 'error.txt').write_text(traceback.format_exc(), encoding='utf8')
        raise


def check_statistics(folder):
    import numpy as np
    import polars as pl
    from . import stats

    # exercise the bundled scientific libraries too
    rng, rows = np.random.default_rng(42), []
    for group in range(2):
        for animal in range(8):
            offset = rng.normal(0, 2)
            for day in range(4):
                rows.append({'group': str(group), 'lot': str(group), 'animal': animal, 'day': day,
                             'y': 10 + offset + group * 2 + day + rng.normal()})
    data = pl.DataFrame(rows)
    _, pairwise = stats.compare_groups(data.filter(day=0), 'y', ['0', '1'])
    if pairwise.height != 1 or pairwise['p'].null_count() or not pairwise['p'].is_between(0, 1).all():
        raise RuntimeError('Bundled group comparison failed')
    pairwise.write_csv(folder / 'group-test.csv')
    for test in ('mixed_anova', 'lmm'):
        omnibus, posthoc = stats.repeated_measures(data, 'y', 'day', ['0', '1'], test=test)
        if (omnibus.height != 3 or omnibus['p'].null_count() or not omnibus['p'].is_between(0, 1).all()
                or posthoc.height != 4 or posthoc['p_adj'].null_count() or not posthoc['p_adj'].is_between(0, 1).all()):
            raise RuntimeError(f'Bundled {test} comparison failed')
        omnibus.write_csv(folder / f'{test}.csv')
        posthoc.write_csv(folder / f'{test}-posthoc.csv')
