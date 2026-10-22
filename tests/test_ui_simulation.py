import pytest
from nicegui import ui

from tests.ui_simulation import user_simulation


@pytest.mark.asyncio
async def test_simulation_waits_for_synchronous_page_setup():
    def root():
        ui.label('ready')
    async with user_simulation(root) as user:
        client = await user.open('/')
        assert client is user.client
        await user.should_see('ready')


@pytest.mark.asyncio
async def test_simulation_propagates_page_setup_errors():
    async def root():
        raise ValueError('page setup failed')
    async with user_simulation(root) as user:
        with pytest.raises(ValueError, match='page setup failed'):
            await user.open('/')
