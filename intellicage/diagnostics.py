import json
from sys import platform

from nicegui import run, ui

from .desktop import error_message
from .native_errors import copy_text
from .windows_startup import error_report


def show_error(error, message=None):
    report = error_report(error)
    with ui.dialog().props('persistent') as dialog, ui.card().classes('w-[700px] max-w-full'):
        ui.label('could not finish').classes('text-lg font-bold')
        ui.label(message or error_message(error)).classes('whitespace-pre-wrap break-words')
        ui.label('Copy the log and paste it into your message to Arina.')
        with ui.expansion('log details').classes('w-full') as details:
            ui.textarea(value=report).props('readonly rows=10').classes('w-full font-mono').mark('error-log')
        status = ui.label().classes('text-sm').mark('copy-log-status')
        async def copy():
            try:
                if platform in ('win32', 'darwin'):
                    await run.io_bound(copy_text, report)
                else:
                    await ui.run_javascript(f'await navigator.clipboard.writeText({json.dumps(report)}); return true;')
                status.set_text('log copied')
            except Exception:
                details.open()
                status.set_text('Could not copy. Select the log above or download it.')
        with ui.row():
            ui.button('copy log', icon='content_copy', on_click=copy).mark('copy-log')
            ui.button('download log', on_click=lambda: ui.download.content(
                report, 'intellicage-error.log', 'text/plain')).props('flat').mark('download-log')
            ui.button('close', on_click=dialog.close).props('flat')
    dialog.open()
    return dialog
