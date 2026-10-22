import asyncio
import inspect
from contextlib import asynccontextmanager

from nicegui import ui
from nicegui.testing.user_simulation import user_simulation as nicegui_simulation


@asynccontextmanager
async def user_simulation(root):
    ready = {}

    async def connected_root():
        client = ui.context.client
        finished = asyncio.get_running_loop().create_future()
        ready[client.id] = finished
        # deliver the page before preparing data and graphs
        await client.connected()
        try:
            result = root()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            finished.cancel()
            raise
        except Exception as exc:
            if not finished.done(): finished.set_exception(exc)
        else:
            if not finished.done(): finished.set_result(None)

    async with nicegui_simulation(connected_root) as user:
        original_open = user.open
        async def open_page(path, *, clear_forward_history=True):
            client = await original_open(path, clear_forward_history=clear_forward_history)
            await asyncio.wait_for(asyncio.shield(ready[client.id]), timeout=30)
            return client
        user.open = open_page
        try:
            yield user
        finally:
            user.open = original_open
            for finished in ready.values():
                if not finished.done(): finished.cancel()
