import asyncio
import copy

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

from intellicage.catalogue import analysis_name
from intellicage.interface import Explorer
from intellicage.project import Project


async def settled(page):
    await asyncio.sleep(.02)
    for _ in range(200):
        if not page.busy and not page.render_lock.locked(): return
        await asyncio.sleep(.01)
    raise AssertionError('View did not finish updating')


def groups(plot):
    return {trace['meta']['group'] for trace in plot.figure['data']}


@pytest.mark.asyncio
async def test_navigation_only_shows_day_night_settings_where_used(config):
    config['analyses'] = 'all'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        assert page.analysis_view.value == 'overview'
        assert not page.window_settings.visible
        assert 'Day / night' not in page.section.options
        assert page.mice_area.style['overflow-y'] == 'auto'
        assert page.mice_area.style['display'] == 'block'
        assert page.mice_expansion.props['caption'] == '6 of 6 selected'
        with user.client:
            page.mice_expansion.open()
            page.mouse_checkboxes[('control', 'lot 1', 1)].set_value(False)
        await settled(page)
        assert page.mice_expansion.props['caption'] == '5 of 6 selected'
        with user.client: await page.install(Project(page.project.configuration()))
        assert page.mice_expansion.value
        assert page.mice_expansion.props['caption'] == '5 of 6 selected'
        with user.client: page.section.set_value('Corners')
        await settled(page)
        assert len(page.graph_select.options) == 5
        with user.client: page.analysis_view.set_value('day / night')
        await settled(page)
        assert page.window_settings.visible
        assert len(page.graph_select.options) == 6
        assert all(page.view_for(a) == 'day / night' for a in page.project.analyses if analysis_name(a) in page.graph_select.options)
        with user.client: page.analysis_view.set_value('hourly activity')
        await settled(page)
        assert not page.window_settings.visible
        assert all(page.view_for(a) == 'hourly activity' for a in page.project.analyses if analysis_name(a) in page.graph_select.options)


@pytest.mark.asyncio
async def test_page_drag_resize_edit_save_and_live_selection(config, tmp_path):
    config['analyses'] = 'all'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        first, second = [a['name'] for a in page.project.analyses[:2]]
        user.find(marker='add-to-page').click()
        with user.client: page.graph_select.set_value(second)
        await settled(page)
        user.find(marker='add-to-page').click()
        user.find(marker='add-to-page').click()
        assert len(page.board.panels) == 2
        assert user.notify.contains('already on my page')
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        assert set(page.board.plots) == {first, second}
        assert page.board.sortable.handle == '.chart-drag-handle'
        for plot in page.board.plots.values():
            assert groups(plot) == {'control', 'ko'}
            assert plot.figure['layout']['autosize']
            assert 'height' not in plot.figure['layout']
            assert not plot.figure['config']['scrollZoom']
        user.find(marker=f'board-size-{first}').trigger('resize', {'width': 720, 'height': 350})
        assert page.board.panels[0]['width'] == 720
        assert page.board.panels[0]['height'] == 350
        container = page.board.container
        user.find(marker='graph-board').trigger('sortend', {
            'item_id': page.board.cards[second].id, 'from_id': container.id, 'to_id': container.id,
            'old_index': 1, 'new_index': 0})
        assert [p['name'] for p in page.board.panels] == [second, first]
        user.find(marker=f'board-plot-{first}').trigger('plotly_relayout', {'title.text': 'my board title'})
        with user.client: page.group_checkboxes['ko'].set_value(False)
        await settled(page)
        assert all(groups(plot) == {'control'} for plot in page.board.plots.values())
        assert page.board.plots[first].figure['layout']['title']['text'] == 'my board title'
        saved = page.project.save()
        with user.client: await page.install(Project.open(saved, tmp_path/'opened'))
        assert [p['name'] for p in page.board.panels] == [second, first]
        assert next(p for p in page.board.panels if p['name'] == first)['width'] == 720
        assert page.board.cards[first].style['height'] == '350px'
        with user.client: await page.open_board_graph(first)
        assert page.workspace_tabs.value == 'graphs'
        assert page.name == first
        assert page.project.result(first)['figure'].layout.title.text == 'my board title'
        with user.client:
            await page.download_graph('html', second)
            page.workspace_tabs.set_value('page')
        await settled(page)
        with user.client:
            await page.board.remove(first)
            await page.board.remove(second)
        assert not page.board.panels
        assert not page.board.plots
        with user.client: page.new_project()
        assert page.workspace_tabs.value == 'graphs'
        assert not any(page.table_content.descendants())


@pytest.mark.asyncio
async def test_tables_follow_filters_and_keep_preview_mode(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        assert not any(page.table_content.descendants())
        with user.client: page.workspace_tabs.set_value('tables')
        await settled(page)
        assert page.table_preview.table.height == 6
        with user.client:
            page.table_preview.mode.set_value('tail')
            page.group_checkboxes['ko'].set_value(False)
        await settled(page)
        assert page.table_preview.mode.value == 'tail'
        assert page.table_preview.table.height == 3
        assert set(page.table_preview.table['group']) == {'control'}
        with user.client: page.table_select.set_value('corner_summary')
        assert page.table_preview.mode.value == 'head'
        assert set(page.table_preview.table['corner']) == {1, 2, 3, 4}
        with user.client: page.group_checkboxes['control'].set_value(False)
        await settled(page)
        assert page.table_preview.table.is_empty()
        assert page.table_preview.grid.options['rowData'] == []


@pytest.mark.asyncio
async def test_page_prunes_invalid_layout_and_refreshes_on_folder_changes(config):
    config = copy.deepcopy(config)
    names = [analysis_name(a) for a in config['analyses']]
    config['workspace'] = {'charts': [None, {'name': 'missing'},
        {'name': names[0], 'width': float('nan'), 'height': -10}, {'name': names[0]},
        {'name': names[1], 'width': 9000, 'height': 420}]}
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        assert len(page.board.panels) == 2
        assert page.board.panels[0]['width'] == 480
        assert page.board.panels[0]['height'] == 300
        assert page.board.panels[1]['width'] == 4000
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        original = page.project.datasets[1].path
        with user.client: await page.remove_folder(original)
        assert all(groups(plot) == {'control'} for plot in page.board.plots.values())
        with user.client: await page.add_folders([original])
        assert all(groups(plot) == {'control', 'ko'} for plot in page.board.plots.values())
        before = copy.deepcopy(page.board.panels)
        with user.client:
            page.board.resized(names[0], {'width': 0, 'height': float('nan')})
            page.board.resized('missing', {'width': 500, 'height': 500})
        assert before == page.board.panels


@pytest.mark.asyncio
async def test_custom_graphs_are_separated_even_when_the_section_name_is_shared(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        hourly = analysis_name(config['analyses'][3])
        assert hourly not in page.graph_select.options
        with user.client: page.analysis_view.set_value('hourly activity')
        await settled(page)
        assert list(page.graph_select.options) == [hourly]
        with user.client: page.analysis_view.set_value('day / night')
        await settled(page)
        assert page.name is None
        assert not page.add_graph_button.enabled
        assert not any(isinstance(e, ui.plotly) for e in page.graph_area.descendants())
        with user.client: page.workspace_tabs.set_value('tables')
        await settled(page)
        assert page.table_preview.table.height == 6


@pytest.mark.asyncio
async def test_failed_calculations_clear_old_plots_and_allow_recovery(config, monkeypatch):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        name, original = page.name, page.project.result
        with user.client: page.board.add(name)
        def broken(_): raise ValueError('calculation failed')
        monkeypatch.setattr(page.project, 'result', broken)
        with user.client: await page.show_graph()
        assert not any(isinstance(e, ui.plotly) for e in page.graph_area.descendants())
        assert user.notify.contains('calculation failed')
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        assert name in page.board.cards
        assert not page.board.plots
        monkeypatch.setattr(page.project, 'result', original)
        with user.client: await page.show_graph()
        assert name in page.board.plots
        assert not page.busy
