import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
import yaml
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.browser


def test_real_browser_edits_filters_and_saves_project(tmp_path, config):
    root = Path(__file__).resolve().parents[1]
    config['analyses'] = 'all'
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    executable = os.getenv('CHROME_PATH') or shutil.which('google-chrome') or shutil.which('chromium')
    if not executable and sys.platform == 'darwin':
        executable = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    if not executable or not Path(executable).is_file():
        pytest.fail('Set CHROME_PATH to a Chrome executable')
    process = subprocess.Popen([sys.executable, 'app.py', '--browser', '--config', str(config_path), '--no-show', '--port', str(port)], cwd=root)
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail('App exited before opening its port')
            try:
                with urlopen(f'http://127.0.0.1:{port}', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable)
            page = browser.new_page(viewport={'width':1440,'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('.js-plotly-plot .main-svg').first.wait_for(timeout=30000)
            plot = page.locator('.js-plotly-plot').first
            plot.scroll_into_view_if_needed()
            axes_before = plot.evaluate('(p) => [p._fullLayout.xaxis.range, p._fullLayout.yaxis.range]')
            scroll_before = page.evaluate('window.scrollY')
            plot.hover()
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(300)
            assert plot.evaluate('(p) => [p._fullLayout.xaxis.range, p._fullLayout.yaxis.range]') == axes_before
            assert page.evaluate('window.scrollY') > scroll_before

            page.get_by_text('edit this graph', exact=True).click()
            page.get_by_label('title', exact=True).fill('browser title')
            page.get_by_role('button',name='apply titles',exact=True).click()
            page.get_by_text('browser title',exact=True).first.wait_for()
            with page.expect_download() as download:
                page.get_by_role('button',name='save project',exact=True).click()
            target=tmp_path/'browser.icage'
            download.value.save_as(target)
            from intellicage.project import Project
            restored=Project.open(target.read_bytes(),tmp_path/'restored')
            assert restored.styles[restored.analyses[0]['name']]['layout']['title.text'] == 'browser title'
            page.screenshot(path=str(tmp_path/'dashboard.png'),full_page=True)
            assert not errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
