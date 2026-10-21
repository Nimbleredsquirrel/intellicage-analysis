import io
import json
import os
import runpy
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import pytest
from PIL import Image, PdfParser

from intellicage import desktop


def mock_renderer(monkeypatch, render):
    monkeypatch.setattr(go.Figure, 'to_image', render)
    monkeypatch.setattr(go.Figure, 'write_image',
                        lambda fig, path, **kwargs: Path(path).write_bytes(render(fig, format='png', **kwargs)))


@pytest.mark.parametrize('platform,frozen,bundle,args', [
    ('darwin', False, True, 3), ('win32', True, True, 3),
    ('darwin', True, False, 3), ('darwin', True, True, 1),
])
def test_normal_launch_is_not_a_chrome_worker(monkeypatch, tmp_path, platform, frozen, bundle, args):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path) if bundle else None, raising=False)
    monkeypatch.setattr(sys, 'argv', ['app', 'other.py', 'chrome'][:args])
    assert not desktop.chrome_worker()


def test_frozen_mac_dispatches_only_its_own_chrome_wrapper(monkeypatch, tmp_path):
    wrapper = tmp_path / 'choreographer/browsers/_unix_pipe_chromium_wrapper.py'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'argv', ['app', str(tmp_path / 'untrusted.py'), 'chrome'])
    assert not desktop.chrome_worker()
    monkeypatch.setitem(sys.modules, 'choreographer.browsers._unix_pipe_chromium_wrapper',
                        types.ModuleType('choreographer.browsers._unix_pipe_chromium_wrapper'))
    monkeypatch.setattr(sys, 'argv', ['app', str(wrapper), '/Applications/Google Chrome', '--headless'])
    assert desktop.chrome_worker()
    assert sys.argv == [str(wrapper), '/Applications/Google Chrome', '--headless']


def test_worker_restores_pipe_streams_instead_of_discarding_them(monkeypatch, tmp_path):
    wrapper = tmp_path / 'choreographer/browsers/_unix_pipe_chromium_wrapper.py'
    opened = []
    def stream(fd, mode, buffering, closefd):
        opened.append((fd, mode, buffering, closefd))
        return io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'frozen', True, raising=False)
        patch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
        patch.setattr(sys, 'platform', 'darwin')
        patch.setattr(sys, 'argv', ['app', str(wrapper), 'chrome'])
        patch.setitem(sys.modules, 'choreographer.browsers._unix_pipe_chromium_wrapper',
                      types.ModuleType('choreographer.browsers._unix_pipe_chromium_wrapper'))
        patch.setattr(sys, 'stdout', None)
        patch.setattr(sys, 'stderr', None)
        patch.setattr(desktop.os, 'fdopen', stream)
        assert desktop.chrome_worker()
        assert sys.stdout is not None and sys.stderr is not None
    assert opened == [(1, 'w', 1, False), (2, 'w', 1, False)]


@pytest.mark.skipif(os.name == 'nt', reason='macos and unix worker pipes')
def test_frozen_worker_keeps_the_actual_parent_child_pipes(tmp_path):
    # a tiny process replaces chrome, no browser is launched
    child = tmp_path / 'pipe check.py'
    child.write_text("import os\nmessage = os.read(3, 1024)\nos.write(4, b'reply: ' + message)\n", encoding='utf8')
    root = Path(__file__).resolve().parents[1]
    wrapper = tmp_path / 'choreographer/browsers/_unix_pipe_chromium_wrapper.py'
    bootstrap = (f"import sys, runpy; sys.path.insert(0, {str(root)!r}); "
                 "sys.frozen = True; sys._MEIPASS = sys.argv.pop(1); "
                 "sys.stdout = None; sys.stderr = None; "
                 f"runpy.run_path({str(root / 'app.py')!r}, run_name='__main__')")
    process = subprocess.run([sys.executable, '-c', bootstrap, str(tmp_path), str(wrapper),
                              sys.executable, str(child)], input='hello\n', text=True,
                             capture_output=True, cwd=tmp_path, timeout=20)
    assert process.returncode == 0, process.stderr
    assert 'reply: hello\n' in process.stdout
    assert '{bye}' in process.stdout


def test_windowed_startup_supplies_missing_streams_without_replacing_existing_ones(monkeypatch):
    original = sys.stdout
    desktop.prepare_stdio()
    assert sys.stdout is original
    streams = []
    with monkeypatch.context() as patch:
        for name in ('stdin', 'stdout', 'stderr'): patch.setattr(sys, name, None)
        desktop.prepare_stdio()
        streams = [sys.stdin, sys.stdout, sys.stderr]
        assert all(stream is not None for stream in streams)
        sys.stdout.write('startup without a console')
        sys.stderr.flush()
        assert sys.stdin.read() == ''
    for stream in streams: stream.close()


@pytest.fixture
def image_renderer(monkeypatch):
    calls = []
    def render(figure, **kwargs):
        calls.append(kwargs)
        if kwargs.get('format') == 'svg':
            return b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="680"/>'
        width = kwargs.get('width') or figure.layout.width
        height = kwargs.get('height') or figure.layout.height
        scale = kwargs.get('scale', 1)
        buffer = io.BytesIO()
        with Image.new('RGB', (round(width * scale), round(height * scale)), 'blue') as image:
            image.save(buffer, format='PNG')
        return buffer.getvalue()
    mock_renderer(monkeypatch, render)
    return calls


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_export_check_validates_images_pdf_and_unicode_tables(monkeypatch, tmp_path, image_renderer, platform):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setitem(sys.modules, 'webview.platforms.winforms', types.SimpleNamespace(renderer='edgechromium'))
    folder = tmp_path / 'результаты проверки'
    desktop.check_exports(folder)
    assert json.loads((folder / 'check.json').read_text(encoding='utf8')) == {'ok': True}
    assert not (folder / 'error.txt').exists()
    assert image_renderer[0]['scale'] == 4
    assert len(PdfParser.PdfParser(f=io.BytesIO((folder / 'dashboard.pdf').read_bytes())).pages) == 1
    for name, separator in [('table.csv', ','), ('table.txt', '\t')]:
        assert pl.read_csv(folder / name, separator=separator)['mouse'][0] == 'мышь 1'
    with zipfile.ZipFile(folder / 'table.xlsx') as workbook:
        assert 'мышь 1' in workbook.read('xl/worksheets/sheet1.xml').decode('utf8')
    assert pl.read_csv(folder / 'group-test.csv').height == 1
    for test in ('mixed_anova', 'lmm'):
        assert pl.read_csv(folder / f'{test}.csv')['p'].is_between(0, 1).all()
        assert pl.read_csv(folder / f'{test}-posthoc.csv').height == 4


def test_export_check_reports_a_missing_windows_webview(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'webview.platforms.winforms', types.SimpleNamespace(renderer='mshtml'))
    with pytest.raises(RuntimeError, match='WebView2'):
        desktop.check_exports(tmp_path)
    assert 'WebView2' in (tmp_path / 'error.txt').read_text(encoding='utf8')
    assert not (tmp_path / 'check.json').exists()


def test_export_check_rejects_incorrect_png_dimensions(monkeypatch, tmp_path):
    def wrong_size(*args, **kwargs):
        buffer = io.BytesIO()
        with Image.new('RGB', (10, 10)) as image: image.save(buffer, format='PNG')
        return buffer.getvalue()
    mock_renderer(monkeypatch, wrong_size)
    with pytest.raises(RuntimeError, match='PNG size'):
        desktop.check_exports(tmp_path)
    assert not (tmp_path / 'check.json').exists()


def test_export_check_keeps_renderer_error_details(monkeypatch, tmp_path):
    def fail(*args, **kwargs): raise RuntimeError('Chrome closed immediately')
    mock_renderer(monkeypatch, fail)
    with pytest.raises(RuntimeError, match='Chrome closed immediately'):
        desktop.check_exports(tmp_path)
    assert 'Chrome closed immediately' in (tmp_path / 'error.txt').read_text(encoding='utf8')
    assert not (tmp_path / 'check.json').exists()


def test_export_check_rejects_incorrect_pdf_page_count(monkeypatch, tmp_path, image_renderer):
    import intellicage.dashboard_report as report
    buffer = io.BytesIO()
    with Image.new('RGB', (100, 100)) as image:
        image.save(buffer, format='PDF', save_all=True, append_images=[image])
    monkeypatch.setattr(report, 'dashboard_pdf', lambda *args: buffer.getvalue())
    with pytest.raises(RuntimeError, match='number of pages'):
        desktop.check_exports(tmp_path)


def test_bootstrap_runs_the_export_check_without_starting_the_gui(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['app', '--check-exports', str(tmp_path)])
    monkeypatch.setattr(desktop, 'check_exports', lambda path: calls.append(path))
    runpy.run_module('app', run_name='__main__')
    assert calls == [str(tmp_path)]


def test_worker_bootstrap_never_opens_another_app_window(monkeypatch):
    monkeypatch.setattr(desktop, 'chrome_worker', lambda: True)
    monkeypatch.setattr(desktop, 'prepare_stdio', lambda: pytest.fail('worker entered the GUI startup path'))
    runpy.run_module('app', run_name='__main__')


def test_browser_errors_have_actionable_messages_and_keep_the_original_cause():
    from choreographer.errors import BrowserFailedError, ChromeNotFoundError

    failure = BrowserFailedError('technical browser details')
    missing = ChromeNotFoundError('technical path details')
    assert 'Chrome could not start' in desktop.error_message(failure)
    assert 'Install Chrome' in desktop.error_message(missing)
    wrapped = RuntimeError('wrapped by plotly')
    wrapped.__cause__ = missing
    assert desktop.error_message(wrapped) == desktop.error_message(missing)
    assert str(wrapped.__cause__) == 'technical path details'
    ordinary = ValueError('bad data')
    ordinary.__context__ = ordinary
    assert desktop.error_message(ordinary) == 'bad data'


@pytest.mark.parametrize('missing', ['group', 'repeated', 'group-null', 'repeated-null'])
def test_bundled_statistics_must_produce_actual_results(monkeypatch, tmp_path, missing):
    from intellicage import stats

    if missing.startswith('group'):
        values = [None] if missing == 'group-null' else []
        monkeypatch.setattr(stats, 'compare_groups', lambda *args: (pl.DataFrame(), pl.DataFrame({'p': values})))
    else:
        unavailable = pl.DataFrame({'p': [.5] * 3 if missing == 'repeated-null' else [None]})
        posthoc = pl.DataFrame({'p_adj': [None] * 4})
        monkeypatch.setattr(stats, 'repeated_measures', lambda *args, **kwargs: (unavailable, posthoc))
    with pytest.raises(RuntimeError, match='Bundled'):
        desktop.check_statistics(tmp_path)
