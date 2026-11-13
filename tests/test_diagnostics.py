import asyncio
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest
from nicegui import app, ui

from intellicage import diagnostics as D, native_errors, windows_startup as W
from intellicage.interface import Explorer
from tests.ui_simulation import user_simulation


@pytest.mark.asyncio
async def test_error_log_can_be_copied_and_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(W, 'log_path', lambda: tmp_path / 'startup.log')
    monkeypatch.setattr('sys.platform', 'darwin')
    copy = Mock()
    monkeypatch.setattr(D, 'copy_text', copy)
    async def root():
        D.show_error(ValueError('bad data · мышь'))
    async with user_simulation(root) as user:
        await user.open('/')
        log = user.find(marker='error-log').elements.pop().value
        assert 'ValueError: bad data · мышь' in log and 'Python:' in log
        user.find(marker='copy-log').click()
        await user.should_see('log copied')
        copy.assert_called_once_with(log)
        download = Mock()
        monkeypatch.setattr(ui.download, 'content', download)
        user.find(marker='download-log').click()
        await asyncio.sleep(0)
        assert download.call_args.args == (log, 'intellicage-error.log', 'text/plain')


@pytest.mark.asyncio
async def test_failed_clipboard_does_not_hide_the_log_or_open_another_error(monkeypatch):
    monkeypatch.setattr('sys.platform', 'darwin')
    monkeypatch.setattr(D, 'copy_text', Mock(side_effect=OSError('clipboard busy')))
    async def root(): D.show_error(ValueError('bad data'))
    async with user_simulation(root) as user:
        await user.open('/')
        user.find(marker='copy-log').click()
        await user.should_see('Could not copy. Select the log above or download it.')
        assert len(user.find(marker='copy-log').elements) == 1


@pytest.mark.asyncio
async def test_non_native_clipboard_awaits_the_javascript_result(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr('sys.platform', 'linux')
    javascript = AsyncMock(return_value=True)
    monkeypatch.setattr(ui, 'run_javascript', javascript)
    async def root(): D.show_error(ValueError('bad data'))
    async with user_simulation(root) as user:
        await user.open('/')
        user.find(marker='copy-log').click()
        await user.should_see('log copied')
        assert 'navigator.clipboard.writeText' in javascript.call_args.args[0]


@pytest.mark.asyncio
async def test_unhandled_interface_error_offers_a_copy_button():
    async def root():
        Explorer(None, local=True, native=True)
        def fail(): raise ValueError('unexpected callback failure')
        ui.button('fail', on_click=fail)
    async with user_simulation(root) as user:
        await user.open('/')
        user.find('fail').click()
        await user.should_see('unexpected callback failure')
        await user.should_see(marker='copy-log')


@pytest.mark.parametrize('failure', [RuntimeError('native worker failed'), SystemExit(1), SystemExit(0)])
def test_native_worker_failure_offers_the_log_before_the_worker_exits(monkeypatch, failure):
    import multiprocessing
    from intellicage import desktop
    monkeypatch.setattr(desktop, 'chrome_worker', lambda: False)
    monkeypatch.setattr(desktop, 'prepare_stdio', lambda: None)
    monkeypatch.setattr(multiprocessing, 'freeze_support', Mock(side_effect=failure))
    report = Mock()
    monkeypatch.setattr(W, 'report_error', report)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(__file__).parents[1] / 'app.py'), run_name='__main__')
    expected = failure.code if isinstance(failure, SystemExit) else 1
    assert error.value.code == expected
    if expected: report.assert_called_once_with(failure)
    else: report.assert_not_called()


def test_packaged_macos_startup_failure_offers_the_system_error_dialog(monkeypatch):
    monkeypatch.setattr(W, 'packaged_windows', lambda: False)
    monkeypatch.setattr('sys.platform', 'darwin')
    monkeypatch.setattr('sys.frozen', True, raising=False)
    dialog = Mock()
    monkeypatch.setattr(native_errors, 'error_dialog', dialog)
    with pytest.raises(ValueError):
        with W.application_session(): raise ValueError('native startup failure')
    assert 'native startup failure' in dialog.call_args.args[0]
