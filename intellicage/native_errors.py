import ctypes
import subprocess
import sys
import time
from ctypes import wintypes as T

TITLE = 'intellicage analysis'


def copy_text(text):
    if sys.platform == 'darwin':
        subprocess.run(['/usr/bin/pbcopy'], input=text.encode('utf8'), check=True, timeout=10)
        return
    if sys.platform != 'win32':
        raise OSError('The system clipboard is unavailable')
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.CreateWindowExW.argtypes = [T.DWORD, T.LPCWSTR, T.LPCWSTR, T.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    T.HWND, T.HMENU, T.HINSTANCE, T.LPVOID]
    user.CreateWindowExW.restype = T.HWND
    user.DestroyWindow.argtypes = [T.HWND]
    user.OpenClipboard.argtypes = [T.HWND]
    user.SetClipboardData.argtypes = [T.UINT, T.HANDLE]
    user.SetClipboardData.restype = T.HANDLE
    kernel.GlobalAlloc.argtypes = [T.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = T.HANDLE
    kernel.GlobalLock.argtypes = [T.HANDLE]
    kernel.GlobalLock.restype = T.LPVOID
    kernel.GlobalUnlock.argtypes = [T.HANDLE]
    kernel.GlobalFree.argtypes = [T.HANDLE]
    kernel.GlobalFree.restype = T.HANDLE
    window = user.CreateWindowExW(0, 'STATIC', TITLE, 0, 0, 0, 0, 0, None, None, None, None)
    memory, opened = None, False
    try:
        if not window: raise OSError('Could not access the clipboard')
        for _ in range(10):
            opened = bool(user.OpenClipboard(window))
            if opened: break
            time.sleep(.02)
        if not opened: raise OSError('The clipboard is busy. Try copying again.')
        data = text.encode('utf-16le') + b'\0\0'
        memory = kernel.GlobalAlloc(2, len(data))
        if not memory: raise OSError('Could not allocate clipboard memory')
        pointer = kernel.GlobalLock(memory)
        if not pointer: raise OSError('Could not lock clipboard memory')
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel.GlobalUnlock(memory)
        if not user.EmptyClipboard() or not user.SetClipboardData(13, memory):
            raise OSError('Could not write to the clipboard')
        # windows owns the memory after setclipboarddata succeeds
        memory = None
    finally:
        if memory: kernel.GlobalFree(memory)
        if opened: user.CloseClipboard()
        if window: user.DestroyWindow(window)


def windows_dialog(detail, report):
    class Button(ctypes.Structure):
        _layout_ = 'ms'
        _pack_ = 1
        _fields_ = [('id', ctypes.c_int), ('text', T.LPCWSTR)]

    class Config(ctypes.Structure):
        _layout_ = 'ms'
        _pack_ = 1
        _fields_ = [('size', T.UINT), ('parent', T.HWND), ('instance', T.HINSTANCE),
                    ('flags', T.UINT), ('common_buttons', T.UINT), ('title', T.LPCWSTR),
                    ('icon', ctypes.c_void_p), ('instruction', T.LPCWSTR), ('content', T.LPCWSTR),
                    ('count', T.UINT), ('buttons', ctypes.POINTER(Button)), ('default', ctypes.c_int),
                    ('radio_count', T.UINT), ('radios', ctypes.c_void_p), ('radio_default', ctypes.c_int),
                    ('verification', T.LPCWSTR), ('expanded', T.LPCWSTR), ('expand_text', T.LPCWSTR),
                    ('collapse_text', T.LPCWSTR), ('footer_icon', ctypes.c_void_p), ('footer', T.LPCWSTR),
                    ('callback', ctypes.c_void_p), ('data', ctypes.c_ssize_t), ('width', T.UINT)]

    buttons = (Button * 1)(Button(100, 'copy log'))
    config = Config(size=ctypes.sizeof(Config), flags=8, common_buttons=32, title=TITLE,
                    instruction='The application could not open its window.',
                    content=f'{detail}\n\nCopy the log, then paste it into your message to Arina.',
                    count=1, buttons=buttons, default=100, expanded=report, expand_text='log details', width=360)
    selected = ctypes.c_int()
    dialog = ctypes.windll.comctl32.TaskDialogIndirect
    dialog.argtypes = [ctypes.POINTER(Config), ctypes.POINTER(ctypes.c_int),
                       ctypes.c_void_p, ctypes.c_void_p]
    dialog.restype = ctypes.c_int32
    if dialog(ctypes.byref(config), ctypes.byref(selected), None, None) < 0:
        raise OSError('The error dialog could not open')
    return selected.value == 100


def error_dialog(detail, report):
    if sys.platform == 'darwin':
        script = ('on run argv\n'
                  'display dialog (item 1 of argv) with title "intellicage analysis" '
                  'buttons {"close", "copy log"} default button "copy log"\nend run')
        choice = subprocess.run(['/usr/bin/osascript', '-e', script,
                                 f'{detail}\n\nCopy the log, then paste it into your message to Arina.'],
                                capture_output=True, text=True, check=True).stdout
        if 'button returned:copy log' in choice: copy_text(report)
        return
    try:
        selected = windows_dialog(detail, report)
    except (AttributeError, OSError):
        selected = ctypes.windll.user32.MessageBoxW(
            None, f'{detail}\n\nCopy the log to the clipboard?', TITLE, 0x24) == 6
    if selected:
        try:
            copy_text(report)
        except OSError:
            ctypes.windll.user32.MessageBoxW(
                None, f'Could not copy the log. Press Ctrl+C to copy this message instead.\n\n{report}', TITLE, 0x10)
