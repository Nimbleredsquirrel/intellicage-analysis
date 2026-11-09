import asyncio
import json
from pathlib import Path

from nicegui import app, run

from .windows_startup import record, report_error


def monitor_window(check_folder=None, timeout=60):
    ready, shown, checking = asyncio.Event(), False, False
    folder = Path(check_folder) if check_folder is not None else None

    def window_shown():
        nonlocal shown
        shown = True
        record('native window shown')

    async def connected(client):
        nonlocal checking
        record('native client connected')
        if checking or ready.is_set(): return
        checking = True
        try:
            async with asyncio.timeout(timeout):
                while True:
                    state = await client.run_javascript('''return {
                        title: document.title,
                        ready: document.body.innerText.includes('intellicage analysis') &&
                            [...document.querySelectorAll('button')].some(b => b.innerText.includes('new project'))
                    };''', timeout=15)
                    if shown and state and state.get('ready'):
                        break
                    await asyncio.sleep(.1)
            record('native interface ready')
            if folder is not None:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'window-check.json').write_text(
                    json.dumps({**state, 'shown': shown, 'connected': True}), encoding='utf8')
            ready.set()
        except Exception as error:
            record(f'window readiness check failed: {error}')
        finally:
            checking = False

    async def watchdog():
        record('desktop server starting')
        try:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
        except TimeoutError:
            error = RuntimeError(f'The application window was not ready after {timeout} seconds.')
            if folder is not None:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'window-error.txt').write_text(str(error), encoding='utf8')
                record(str(error))
            else:
                await run.io_bound(report_error, error)
            app.shutdown()

    def stopped():
        if not ready.is_set(): record('desktop stopped before its interface became ready')
        ready.set()

    app.native.on('shown', window_shown)
    app.native.on('loaded', lambda: record('native page loaded'))
    app.on_connect(connected)
    app.on_startup(watchdog)
    app.on_shutdown(stopped)
