import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from intellicage import desktop, windows_startup as W


@pytest.fixture
def windows(monkeypatch, tmp_path):
    kernel = SimpleNamespace(CreateMutexW=Mock(return_value=101), GetLastError=Mock(return_value=0),
                             CloseHandle=Mock())
    user = SimpleNamespace(MessageBoxW=Mock(return_value=6), FindWindowW=Mock(return_value=0),
                           ShowWindow=Mock(), SetForegroundWindow=Mock())
    monkeypatch.setattr(W.ctypes, 'windll', SimpleNamespace(kernel32=kernel, user32=user), raising=False)
    monkeypatch.setattr(W.ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(W.ctypes, 'set_last_error', Mock(), raising=False)
    monkeypatch.setattr(W.ctypes, 'get_last_error', lambda: kernel.GetLastError(), raising=False)
    monkeypatch.setattr(W.ctypes, 'WinError', lambda: OSError('mutex unavailable'), raising=False)
    monkeypatch.setattr(W, 'packaged_windows', lambda: True)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'данные'))
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(sys, 'argv', ['app'])
    monkeypatch.setitem(sys.modules, 'webview.platforms.winforms', SimpleNamespace(renderer='edgechromium'))
    return kernel, user


def test_only_packaged_windows_uses_startup_helpers(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert not W.packaged_windows()
    monkeypatch.setattr(sys, 'frozen', True)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert not W.packaged_windows()
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert W.packaged_windows()


def test_windowed_errors_are_written_as_utf8_and_workers_append(windows, monkeypatch):
    streams = []
    for args in [['app'], ['app', '--multiprocessing-fork']]:
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'argv', args)
            for name in ('stdin', 'stdout', 'stderr'): patch.setattr(sys, name, None)
            desktop.prepare_stdio()
            streams.extend([sys.stdin, sys.stdout, sys.stderr])
            sys.stderr.write('ошибка окна\n')
            sys.stderr.flush()
            assert sys.stdin.read() == ''
    for stream in streams: stream.close()
    text = W.log_path().read_text(encoding='utf8')
    assert 'starting main' in text and 'starting worker' in text
    assert text.count('ошибка окна') == 2


def test_logs_fall_back_to_temp_when_local_appdata_is_not_a_directory(monkeypatch, tmp_path):
    bad = tmp_path / 'file'
    bad.write_text('not a directory')
    monkeypatch.setenv('LOCALAPPDATA', str(bad))
    monkeypatch.setattr(W.tempfile, 'gettempdir', lambda: str(tmp_path / 'temporary'))
    path = W.log_path()
    assert path.is_relative_to(tmp_path / 'temporary')
    W.record('startup failure')
    assert 'startup failure' in path.read_text(encoding='utf8')


def test_unavailable_log_storage_does_not_replace_the_original_problem(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(Path, 'mkdir', Mock(side_effect=PermissionError('blocked')))
    assert W.log_path() is None
    W.record('error')
    opened = []
    with monkeypatch.context() as patch:
        for name in ('stdin', 'stdout', 'stderr'): patch.setattr(sys, name, None)
        W.prepare_log_streams()
        opened = [sys.stdin, sys.stdout, sys.stderr]
        sys.stderr.write('error')
    for stream in opened: stream.close()


def test_log_write_failure_is_not_a_startup_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(W, 'log_path', lambda: tmp_path / 'startup.log')
    monkeypatch.setattr(Path, 'open', Mock(side_effect=PermissionError('locked')))
    W.record('error')


def test_error_dialog_points_to_the_log(windows):
    _, user = windows
    W.report_error(ValueError('window engine failed'))
    text = user.MessageBoxW.call_args.args[1]
    assert 'window engine failed' in text and str(W.log_path()) in text
    assert 'ValueError: window engine failed' in W.log_path().read_text(encoding='utf8')


def test_error_dialog_without_log_storage(windows, monkeypatch):
    _, user = windows
    monkeypatch.setattr(W, 'log_path', lambda: None)
    W.report_error(ValueError('window engine failed'))
    assert 'window engine failed' in user.MessageBoxW.call_args.args[1]


@pytest.mark.parametrize('user_version,machine_version,expected', [
    ('132.0.1.2', None, '132.0.1.2'),
    (None, '132.0.1.2', '132.0.1.2'),
    ('0.0.0.0', '132.0.1.2', '132.0.1.2'),
    ('unknown', '', None),
])
def test_webview_detection_checks_user_and_machine_registry(monkeypatch, user_version, machine_version, expected):
    versions = {'user': user_version, 'machine': machine_version}
    def open_key(hive, *args):
        if versions[hive] is None: raise FileNotFoundError()
        return nullcontext(hive)
    registry = SimpleNamespace(HKEY_CURRENT_USER='user', HKEY_LOCAL_MACHINE='machine', KEY_READ=1,
                               KEY_WOW64_32KEY=2, OpenKey=open_key,
                               QueryValueEx=lambda hive, name: (versions[hive], 1))
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    assert W.webview_version() == expected


def test_existing_runtime_does_not_prompt_or_install(windows, monkeypatch):
    _, user = windows
    monkeypatch.setattr(W, 'webview_version', lambda: '132.0.1.2')
    assert W.ensure_webview()
    user.MessageBoxW.assert_not_called()


def test_missing_runtime_fails_automated_checks_without_a_dialog(windows, monkeypatch):
    _, user = windows
    monkeypatch.setattr(W, 'webview_version', lambda: None)
    with pytest.raises(RuntimeError, match='missing'): W.ensure_webview(interactive=False)
    user.MessageBoxW.assert_not_called()


def test_runtime_installation_can_be_declined(windows, monkeypatch):
    _, user = windows
    user.MessageBoxW.return_value = 7
    monkeypatch.setattr(W, 'webview_version', lambda: None)
    assert W.ensure_webview() is False


def test_runtime_installer_must_be_in_the_bundle(windows, monkeypatch):
    monkeypatch.setattr(W, 'webview_version', lambda: None)
    with pytest.raises(RuntimeError, match='complete Windows ZIP'): W.ensure_webview()


@pytest.mark.parametrize('installed', [True, False])
def test_runtime_install_is_checked_after_the_official_installer_exits(windows, monkeypatch, tmp_path, installed):
    installer = tmp_path / 'bootstrap' / 'MicrosoftEdgeWebview2Setup.exe'
    installer.parent.mkdir()
    installer.write_bytes(b'installer fixture')
    versions = iter([None, '132.0.1.2' if installed else None])
    monkeypatch.setattr(W, 'webview_version', lambda: next(versions))
    launch = Mock(return_value=SimpleNamespace(returncode=0 if installed else 1))
    monkeypatch.setattr(W.subprocess, 'run', launch)
    if installed:
        assert W.ensure_webview()
    else:
        with pytest.raises(RuntimeError, match='exit code 1'): W.ensure_webview()
    assert launch.call_args.args[0] == [str(installer), '/silent', '/install']
    assert launch.call_args.kwargs['timeout'] == 300


def test_source_and_help_launches_do_not_acquire_a_mutex(windows, monkeypatch):
    kernel, _ = windows
    monkeypatch.setattr(W, 'packaged_windows', lambda: False)
    with W.application_session() as launch: assert launch
    monkeypatch.setattr(W, 'packaged_windows', lambda: True)
    monkeypatch.setattr(sys, 'argv', ['app', '--help'])
    with W.application_session() as launch: assert launch
    kernel.CreateMutexW.assert_not_called()


def test_main_instance_holds_its_mutex_until_the_window_exits(windows, monkeypatch):
    kernel, _ = windows
    monkeypatch.setattr(W, 'ensure_webview', lambda **kwargs: True)
    with W.application_session() as launch:
        assert launch
        kernel.CloseHandle.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(101)


@pytest.mark.parametrize('visible', [False, True])
def test_duplicate_launch_reuses_the_existing_window_without_another_server(windows, monkeypatch, visible):
    kernel, user = windows
    kernel.GetLastError.return_value = 183
    user.FindWindowW.return_value = 202 if visible else 0
    setup = Mock()
    monkeypatch.setattr(W, 'ensure_webview', setup)
    with W.application_session() as launch: assert not launch
    setup.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(101)
    if visible:
        user.ShowWindow.assert_called_once_with(202, 9)
        user.SetForegroundWindow.assert_called_once_with(202)
    else:
        assert 'already starting or running' in user.MessageBoxW.call_args.args[1]


def test_cancelling_setup_exits_quietly(windows, monkeypatch):
    kernel, user = windows
    monkeypatch.setattr(W, 'ensure_webview', lambda **kwargs: False)
    with W.application_session() as launch: assert not launch
    user.MessageBoxW.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(101)


@pytest.mark.parametrize('failure', ['runtime', 'renderer', 'app', 'mutex'])
def test_startup_failures_are_reported_and_release_the_mutex(windows, monkeypatch, failure):
    kernel, _ = windows
    report = Mock()
    monkeypatch.setattr(W, 'report_error', report)
    setup = Mock(return_value=True)
    monkeypatch.setattr(W, 'ensure_webview', setup)
    if failure == 'runtime': setup.side_effect = RuntimeError('runtime missing')
    if failure == 'renderer':
        monkeypatch.setitem(sys.modules, 'webview.platforms.winforms', SimpleNamespace(renderer='mshtml'))
    if failure == 'mutex': kernel.CreateMutexW.return_value = 0
    with pytest.raises(SystemExit) as exit:
        with W.application_session():
            if failure == 'app': raise ValueError('app failed')
    assert exit.value.code == 1
    report.assert_called_once()
    if failure == 'mutex': kernel.CloseHandle.assert_not_called()
    else: kernel.CloseHandle.assert_called_once_with(101)


def test_native_ci_failures_write_logs_instead_of_showing_a_dialog(windows, monkeypatch):
    _, user = windows
    monkeypatch.setattr(sys, 'argv', ['app', '--check-window', 'results'])
    monkeypatch.setattr(W, 'ensure_webview', Mock(side_effect=RuntimeError('runtime missing')))
    with pytest.raises(SystemExit):
        with W.application_session(): pass
    user.MessageBoxW.assert_not_called()
    assert 'runtime missing' in W.log_path().read_text(encoding='utf8')


@pytest.mark.parametrize('code', [0, 1])
def test_library_exit_codes_do_not_hide_startup_failures(windows, monkeypatch, code):
    _, user = windows
    monkeypatch.setattr(W, 'ensure_webview', lambda **kwargs: True)
    with pytest.raises(SystemExit) as error:
        with W.application_session():
            raise SystemExit(code)
    assert error.value.code == code
    if code:
        assert 'exit code 1' in user.MessageBoxW.call_args.args[1]
        assert 'SystemExit: 1' in W.log_path().read_text(encoding='utf8')
    else:
        user.MessageBoxW.assert_not_called()
