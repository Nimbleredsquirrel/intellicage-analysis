import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from intellicage import native_errors as N, windows_startup as W


@pytest.fixture
def clipboard(monkeypatch):
    storage = ctypes.create_string_buffer(1024)
    user = SimpleNamespace(**{name: Mock(return_value=1) for name in
        ('DestroyWindow', 'OpenClipboard', 'CloseClipboard', 'EmptyClipboard', 'SetClipboardData')})
    user.CreateWindowExW = Mock(return_value=202)
    kernel = SimpleNamespace(GlobalAlloc=Mock(return_value=101), GlobalLock=Mock(return_value=ctypes.addressof(storage)),
                             GlobalUnlock=Mock(), GlobalFree=Mock())
    monkeypatch.setattr(N.ctypes, 'windll', SimpleNamespace(user32=user, kernel32=kernel), raising=False)
    monkeypatch.setattr(N.sys, 'platform', 'win32')
    monkeypatch.setattr(N.time, 'sleep', Mock())
    return user, kernel, storage


def test_native_clipboard_preserves_unicode_and_transfers_memory_ownership(clipboard):
    user, kernel, storage = clipboard
    text = 'mouse · мышь 🐭\nerror log'
    N.copy_text(text)
    assert storage.raw[:len(text.encode('utf-16le'))].decode('utf-16le') == text
    user.SetClipboardData.assert_called_once_with(13, 101)
    kernel.GlobalFree.assert_not_called()
    user.CloseClipboard.assert_called_once()
    user.DestroyWindow.assert_called_once_with(202)


@pytest.mark.parametrize('failure', ['window', 'busy', 'allocation', 'lock', 'empty', 'write'])
def test_clipboard_failures_release_owned_resources(clipboard, failure):
    user, kernel, _ = clipboard
    target = {'window': user.CreateWindowExW, 'busy': user.OpenClipboard,
              'allocation': kernel.GlobalAlloc, 'lock': kernel.GlobalLock,
              'empty': user.EmptyClipboard, 'write': user.SetClipboardData}[failure]
    target.return_value = 0
    with pytest.raises(OSError): N.copy_text('error')
    assert user.CloseClipboard.call_count == (0 if failure in ('window', 'busy') else 1)
    assert kernel.GlobalFree.call_count == (1 if failure in ('lock', 'empty', 'write') else 0)
    assert user.DestroyWindow.call_count == (0 if failure == 'window' else 1)
    if failure == 'busy': assert user.OpenClipboard.call_count == 10


def test_clipboard_retries_when_another_program_temporarily_owns_it(clipboard):
    user, _, _ = clipboard
    user.OpenClipboard.side_effect = [0, 1]
    N.copy_text('error')
    assert user.OpenClipboard.call_count == 2


def test_macos_clipboard_receives_utf8_without_shell_interpolation(monkeypatch):
    monkeypatch.setattr(N.sys, 'platform', 'darwin')
    execute = Mock()
    monkeypatch.setattr(N.subprocess, 'run', execute)
    N.copy_text('мышь $(echo x)')
    assert execute.call_args.args[0] == ['/usr/bin/pbcopy']
    assert execute.call_args.kwargs['input'] == 'мышь $(echo x)'.encode()


def test_unsupported_native_clipboard_reports_an_error(monkeypatch):
    monkeypatch.setattr(N.sys, 'platform', 'linux')
    with pytest.raises(OSError, match='unavailable'): N.copy_text('error')


@pytest.mark.parametrize('selection,expected', [(100, True), (8, False)])
def test_windows_error_dialog_offers_copyable_full_details(monkeypatch, selection, expected):
    def display(config, selected, *_):
        data = config._obj
        assert data.title == 'intellicage analysis'
        assert 'window failed' in data.content
        assert data.expanded == 'full traceback'
        assert data.buttons[0].text == 'copy log'
        selected._obj.value = selection
        return 0
    dialog = Mock(side_effect=display)
    monkeypatch.setattr(N.ctypes, 'windll', SimpleNamespace(comctl32=SimpleNamespace(TaskDialogIndirect=dialog)), raising=False)
    assert N.windows_dialog('window failed', 'full traceback') is expected


def test_failed_task_dialog_can_use_the_standard_system_dialog(monkeypatch):
    dialog = Mock(return_value=-1)
    monkeypatch.setattr(N.ctypes, 'windll', SimpleNamespace(comctl32=SimpleNamespace(TaskDialogIndirect=dialog)), raising=False)
    with pytest.raises(OSError, match='could not open'): N.windows_dialog('error', 'report')


@pytest.mark.parametrize('selected', [False, True])
def test_native_copy_is_only_requested_by_the_user(monkeypatch, selected):
    monkeypatch.setattr(N.sys, 'platform', 'win32')
    monkeypatch.setattr(N, 'windows_dialog', lambda *args: selected)
    copy = Mock()
    monkeypatch.setattr(N, 'copy_text', copy)
    N.error_dialog('error', 'traceback')
    assert copy.call_count == int(selected)
    if selected: copy.assert_called_once_with('traceback')


def test_native_dialog_failure_keeps_the_log_available(monkeypatch):
    monkeypatch.setattr(N.sys, 'platform', 'win32')
    monkeypatch.setattr(N, 'windows_dialog', Mock(side_effect=OSError('unavailable')))
    message = Mock(return_value=6)
    monkeypatch.setattr(N.ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(MessageBoxW=message)), raising=False)
    monkeypatch.setattr(N, 'copy_text', Mock(side_effect=OSError('busy')))
    N.error_dialog('error', 'traceback')
    assert 'Ctrl+C' in message.call_args.args[1] and 'traceback' in message.call_args.args[1]


@pytest.mark.parametrize('selected', ['close', 'copy log'])
def test_macos_error_dialog_does_not_need_the_window_library(monkeypatch, selected):
    monkeypatch.setattr(N.sys, 'platform', 'darwin')
    run = Mock(return_value=SimpleNamespace(stdout=f'button returned:{selected}'))
    monkeypatch.setattr(N.subprocess, 'run', run)
    copy = Mock()
    monkeypatch.setattr(N, 'copy_text', copy)
    N.error_dialog('ошибка', 'report')
    assert run.call_args.args[0][:2] == ['/usr/bin/osascript', '-e']
    assert 'ошибка' in run.call_args.args[0][-1]
    assert copy.call_count == int(selected == 'copy log')


def test_error_report_keeps_the_current_traceback_even_without_log_storage(monkeypatch):
    monkeypatch.setattr(W, 'log_path', lambda: None)
    try:
        raise ValueError('мышь')
    except ValueError as error:
        report = W.error_report(error)
    assert 'Traceback' in report and 'ValueError: мышь' in report
    assert 'OS:' in report and 'Python:' in report


def test_error_report_reads_only_the_recent_log_tail(monkeypatch, tmp_path):
    path = tmp_path / 'startup.log'
    path.write_text('old data\n' + 'x' * 100000 + '\nrecent data', encoding='utf8')
    monkeypatch.setattr(W, 'log_path', lambda: path)
    report = W.error_report(ValueError('new error'))
    assert 'old data' not in report and 'recent data' in report and 'new error' in report
    assert len(report) < 68000


def test_unreadable_log_does_not_hide_the_current_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(W, 'log_path', lambda: tmp_path)
    assert 'new error' in W.error_report(ValueError('new error'))
