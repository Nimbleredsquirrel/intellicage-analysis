import asyncio
from threading import Event

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

from intellicage.interface import Explorer
from intellicage.project import Project


async def settled(page):
    await asyncio.sleep(.01)
    for _ in range(200):
        if not page.busy and not page.render_lock.locked():
            return
        await asyncio.sleep(.01)
    raise AssertionError('Selection did not finish rendering')


def plotted_groups(page):
    plot = next(e for e in page.graph_area.descendants() if isinstance(e, ui.plotly))
    return {trace['meta']['group'] for trace in plot.figure['data']}


@pytest.mark.asyncio
async def test_checkboxes_include_mice_and_refresh_without_apply(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        assert all(box.value for box in page.group_checkboxes.values())
        assert all(box.value for box in page.mouse_checkboxes.values())
        assert isinstance(page.phase_select, ui.radio)
        assert not page.mice_expansion.value
        assert all(box in list(page.mice_expansion.descendants()) for box in page.mouse_checkboxes.values())
        assert not any(isinstance(e, ui.button) and e.text == 'apply selection' for e in page.filters.descendants())
        with user.client: page.mice_expansion.open()
        user.find(marker='mouse-0').click()
        await settled(page)
        assert page.project.selection['exclude_mice'] == [{'group': 'control', 'lot': 'lot 1', 'animal': 1}]
        user.find(marker='group-1').click()
        await settled(page)
        assert plotted_groups(page) == {'control'}
        assert len(page.mouse_checkboxes) == 3
        assert not page.mouse_checkboxes[('control', 'lot 1', 1)].value
        user.find(marker='clear-all-mice').click()
        await settled(page)
        assert page.project.result(page.name)['data'].is_empty()
        assert plotted_groups(page) == set()
        user.find(marker='select-all-mice').click()
        await settled(page)
        assert page.project.result(page.name)['data'].height == 3
        user.find(marker='group-0').click()
        await settled(page)
        assert page.project.selection['groups'] == []
        assert not page.mouse_checkboxes
        assert plotted_groups(page) == set()
        user.find(marker='group-1').click()
        await settled(page)
        assert plotted_groups(page) == {'ko'}


@pytest.mark.asyncio
async def test_phase_and_bulk_choices_only_change_visible_mice(config, export_factory, tmp_path):
    path = export_factory('control', 'lot 3', 'phase 2')
    config['datasets'].append({'path': str(path), 'group': 'control', 'lot': 'lot 3', 'phase': 'phase 2'})
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client: page.mice_expansion.open()
        user.find(marker='mouse-0').click()
        await settled(page)
        with user.client: page.phase_select.set_value('phase 2')
        await settled(page)
        assert set(page.mouse_checkboxes) == {('control', 'lot 3', i) for i in (1, 2, 3)}
        assert all(box.value for box in page.mouse_checkboxes.values())
        user.find(marker='clear-all-mice').click()
        await settled(page)
        assert len(page.project.selection['exclude_mice']) == 4
        user.find(marker='select-all-mice').click()
        await settled(page)
        assert page.project.selection['exclude_mice'] == [{'group': 'control', 'lot': 'lot 1', 'animal': 1}]
        saved = page.project.save()
        with user.client:
            page.phase_select.set_value('phase 1')
        await settled(page)
        assert not page.mouse_checkboxes[('control', 'lot 1', 1)].value
        with user.client: await page.install(Project.open(saved, tmp_path/'opened'))
        assert page.phase_select.value == 'phase 2'
        assert all(box.value for box in page.mouse_checkboxes.values())
        assert plotted_groups(page) == {'control'}


@pytest.mark.asyncio
async def test_folder_label_editor_preserves_checked_mice(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        graph = page.name
        page.project.edit_layout(graph, {'title.text': 'my title'})
        with user.client: page.mice_expansion.open()
        user.find(marker='mouse-0').click()
        await settled(page)
        user.find(marker='edit-labels-0').click()
        assert page.metadata_dialog.value
        with user.client:
            page.label_inputs[0].set_value('renamed')
            page.label_inputs[1].set_value('new lot')
            page.label_inputs[2].set_value('new phase')
            await page.relabel()
        assert not page.metadata_dialog.value
        assert page.name == graph
        assert page.project.result(graph)['figure'].layout.title.text == 'my title'
        assert page.group_checkboxes['renamed'].value
        # the other export still belongs to phase 1
        with user.client: page.phase_select.set_value('new phase')
        await settled(page)
        assert not page.mouse_checkboxes[('renamed', 'new lot', 1)].value
        assert page.mouse_checkboxes[('renamed', 'new lot', 2)].value
        assert plotted_groups(page) == {'renamed'}


@pytest.mark.asyncio
async def test_removing_a_folder_preserves_an_intentionally_empty_group_selection(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client:
            for box in page.group_checkboxes.values(): box.set_value(False)
        await settled(page)
        with user.client: await page.remove_folder(page.project.datasets[0].path)
        assert page.project.selection['groups'] == []
        assert not any(box.value for box in page.group_checkboxes.values())
        assert not page.mouse_checkboxes
        assert plotted_groups(page) == set()


@pytest.mark.asyncio
async def test_old_selection_cannot_repopulate_the_current_cache(config, monkeypatch):
    import intellicage.pipeline as service
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        started, release = Event(), Event()
        render = service.render_analysis
        def slow(*args, **kwargs):
            result = render(*args, **kwargs)
            if 'ko' in args[-1]['groups']:
                started.set()
                assert release.wait(5)
            return result
        monkeypatch.setattr(service, 'render_analysis', slow)
        page.project.cache = {}
        with user.client:
            old = asyncio.create_task(page.show_graph())
            try:
                for _ in range(200):
                    if started.is_set(): break
                    await asyncio.sleep(.01)
                assert started.is_set()
                user.find(marker='group-1').click()
            finally:
                release.set()
            await old
        await settled(page)
        assert plotted_groups(page) == {'control'}
        assert set(page.project.cache[page.name]['data']['group']) == {'control'}
