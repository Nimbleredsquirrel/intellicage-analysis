from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

TITLE = 'intellicage analysis'


def packaged_windows():
    return sys.platform == 'win32' and getattr(sys, 'frozen', False)


def log_path():
    for base in [os.environ.get('LOCALAPPDATA'), tempfile.gettempdir()]:
        if not base: continue
        path = Path(base) / TITLE / 'logs' / 'startup.log'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            return path
        except OSError:
            continue
    return None


def record(message):
    path = log_path()
    if path is not None:
        try:
            with path.open('a', encoding='utf8') as output:
                output.write(f'{datetime.now().isoformat(timespec="seconds")} pid={os.getpid()} {message}\n')
        except OSError:
            pass


def prepare_log_streams():
    path = log_path()
    for name, mode in [('stdin', 'r'), ('stdout', 'a'), ('stderr', 'a')]:
        if getattr(sys, name) is None:
            target = path if name != 'stdin' and path is not None else os.devnull
            setattr(sys, name, open(target, mode, encoding='utf8', buffering=1))
    role = 'worker' if '--multiprocessing-fork' in sys.argv else 'main'
    record(f'starting {role}; python={sys.version.split()[0]}')


def message(text, flags=0x10):
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)


def report_error(error):
    record(''.join(traceback.format_exception(error)))
    path = log_path()
    detail = f'Startup ended with exit code {error.code}.' if isinstance(error, SystemExit) else str(error)
    text = f'The application could not open its window.\n\n{detail}'
    if path is not None: text += f'\n\nPlease send this log file to Arina:\n{path}'
    message(text)


def webview_version():
    import winreg

    key = r'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as registry:
                version = str(winreg.QueryValueEx(registry, 'pv')[0])
                if any(int(part) for part in version.split('.')): return version
        except (OSError, ValueError):
            continue
    return None


def ensure_webview(*, interactive=True):
    version = webview_version()
    if version:
        record(f'WebView2 {version}')
        return True
    if not interactive:
        raise RuntimeError('Microsoft Edge WebView2 Runtime is missing')
    question = ('This app needs a Microsoft component to display its window.\n\n'
                'Install Microsoft Edge WebView2 now? Internet access is required.')
    if message(question, 0x24) != 6:
        record('WebView2 installation cancelled')
        return False
    installer = Path(sys._MEIPASS) / 'bootstrap' / 'MicrosoftEdgeWebview2Setup.exe'
    if not installer.is_file():
        raise RuntimeError('The Microsoft component installer is missing. Please download the complete Windows ZIP again.')
    record('installing WebView2')
    result = subprocess.run([str(installer), '/silent', '/install'], timeout=300,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    version = webview_version()
    if not version:
        raise RuntimeError(f'WebView2 could not be installed (exit code {result.returncode}). Please ask your IT team for help.')
    record(f'WebView2 installed: {version}')
    return True


@contextmanager
def application_session():
    if not packaged_windows() or '--help' in sys.argv or '-h' in sys.argv:
        yield True
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = None
    try:
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, False, r'Local\intellicage-analysis')
        exists = ctypes.get_last_error() == 183
        if not handle:
            raise ctypes.WinError()
        if exists:
            record('another instance is already running')
            user = ctypes.windll.user32
            user.FindWindowW.restype = wintypes.HWND
            user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            window = user.FindWindowW(None, TITLE)
            if window:
                user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                user.SetForegroundWindow.argtypes = [wintypes.HWND]
                user.ShowWindow(window, 9)
                user.SetForegroundWindow(window)
            else:
                message('The app is already starting or running. Please wait for its window.\n\n'
                        f'If no window appears, send this log to Arina:\n{log_path()}', 0x40)
            yield False
            return
        if not ensure_webview(interactive='--check-window' not in sys.argv):
            yield False
            return
        from webview.platforms.winforms import renderer
        if renderer != 'edgechromium':
            raise RuntimeError('The Windows window engine could not load. WebView2 and Microsoft .NET Framework 4.6.2 or newer are required.')
        yield True
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (0, None): raise
        if '--check-window' in sys.argv:
            record(''.join(traceback.format_exception(error)))
        else:
            report_error(error)
        raise SystemExit(1) from error
    finally:
        if handle: kernel.CloseHandle(handle)
