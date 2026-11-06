import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from intellicage import window_monitor as M


@pytest.fixture
def monitor(monkeypatch):
    handlers = {}
    app = SimpleNamespace(native=SimpleNamespace(on=lambda name, handler: handlers.update({name: handler})),
                          on_connect=lambda handler: handlers.update(connected=handler),
                          on_startup=lambda handler: handlers.update(startup=handler),
                          on_shutdown=lambda handler: handlers.update(shutdown=handler), shutdown=Mock())
    record, report = Mock(), Mock()
    async def background(function, *args): return function(*args)
    monkeypatch.setattr(M, 'app', app)
    monkeypatch.setattr(M, 'record', record)
    monkeypatch.setattr(M, 'report_error', report)
    monkeypatch.setattr(M.run, 'io_bound', background)
    return handlers, app, record, report


@pytest.mark.asyncio
async def test_native_readiness_requires_a_shown_window_and_rendered_interface(monitor, tmp_path):
    handlers, app, record, report = monitor
    M.monitor_window(tmp_path, timeout=2)
    client = SimpleNamespace(run_javascript=AsyncMock(side_effect=[
        {'title': 'intellicage analysis', 'ready': False},
        {'title': 'intellicage analysis', 'ready': True}]))
    handlers['shown']()
    await handlers['connected'](client)
    await handlers['startup']()
    assert json.loads((tmp_path / 'window-check.json').read_text()) == {
        'title': 'intellicage analysis', 'ready': True, 'shown': True, 'connected': True}
    assert client.run_javascript.await_count == 2
    await handlers['connected'](client)
    assert client.run_javascript.await_count == 2
    app.shutdown.assert_not_called()
    report.assert_not_called()


@pytest.mark.asyncio
async def test_window_event_must_arrive_even_if_the_client_connects_first(monitor):
    handlers, app, _, _ = monitor
    M.monitor_window(timeout=2)
    client = SimpleNamespace(run_javascript=AsyncMock(return_value={'ready': True}))
    asyncio.get_running_loop().call_soon(handlers['shown'])
    await handlers['connected'](client)
    await handlers['startup']()
    assert client.run_javascript.await_count >= 2
    app.shutdown.assert_not_called()


@pytest.mark.asyncio
async def test_parallel_connections_do_not_run_duplicate_startup_checks(monitor):
    handlers, app, _, _ = monitor
    M.monitor_window(timeout=2)
    entered, release = asyncio.Event(), asyncio.Event()
    async def state(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'ready': True}
    client = SimpleNamespace(run_javascript=AsyncMock(side_effect=state))
    handlers['shown']()
    first = asyncio.create_task(handlers['connected'](client))
    await asyncio.wait_for(entered.wait(), 1)
    await handlers['connected'](client)
    assert client.run_javascript.await_count == 1
    release.set()
    await first
    await handlers['startup']()
    app.shutdown.assert_not_called()


@pytest.mark.asyncio
async def test_failed_ui_load_does_not_report_a_successful_window(monitor, tmp_path):
    handlers, app, record, report = monitor
    M.monitor_window(tmp_path, timeout=.02)
    client = SimpleNamespace(run_javascript=AsyncMock(side_effect=RuntimeError('javascript unavailable')))
    handlers['shown']()
    await handlers['connected'](client)
    await handlers['startup']()
    assert not (tmp_path / 'window-check.json').exists()
    assert 'not ready' in (tmp_path / 'window-error.txt').read_text()
    assert any('javascript unavailable' in call.args[0] for call in record.call_args_list)
    report.assert_not_called()
    app.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_ui_readiness_timeout_is_logged(monitor, tmp_path):
    handlers, app, record, _ = monitor
    M.monitor_window(tmp_path, timeout=.01)
    client = SimpleNamespace(run_javascript=AsyncMock(return_value={'ready': False}))
    await handlers['connected'](client)
    await handlers['startup']()
    assert any('readiness check failed' in call.args[0] for call in record.call_args_list)
    app.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_invisible_window_shows_a_diagnostic_instead_of_waiting_forever(monitor):
    handlers, app, _, report = monitor
    M.monitor_window(timeout=.01)
    await handlers['startup']()
    assert 'not ready' in str(report.call_args.args[0])
    app.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_normal_shutdown_cancels_the_startup_watchdog(monitor):
    handlers, app, _, report = monitor
    M.monitor_window(timeout=.01)
    handlers['shutdown']()
    await handlers['startup']()
    app.shutdown.assert_not_called()
    report.assert_not_called()
