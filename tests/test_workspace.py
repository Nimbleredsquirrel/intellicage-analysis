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
        assert user.notify.contains('already on dashboard')
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        assert set(page.board.plots) == {first, second}
        assert all(sortable.handle == '.chart-drag-handle' for sortable in page.board.sortables.values())
        for plot in page.board.plots.values():
            assert groups(plot) == {'control', 'ko'}
            assert plot.figure['layout']['autosize']
            assert 'height' not in plot.figure['layout']
            assert not plot.figure['config']['scrollZoom']
        user.find(marker=f'board-size-{first}').trigger('resize', {'width': 720, 'height': 350})
        assert page.board.panels[0]['width'] == 720
        assert page.board.panels[0]['height'] == 350
        container = next(iter(page.board.group_rows.values()))
        user.find(marker='dashboard-row-1').trigger('sortend', {
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


@pytest.mark.asyncio
async def test_dashboard_picker_keeps_edits_when_graphs_are_removed(config, tmp_path):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        project, board = page.project, page.board
        first, second = [analysis_name(a) for a in project.analyses[:2]]
        project.styles[second] = {'layout': {}, 'traces': {'empty': {}}}
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        assert page.page_tab.label == 'dashboard'
        assert len(board.checkboxes) == len(project.analyses)
        assert not board.is_edited(second)
        user.find(marker='dashboard-choose').click()
        user.find(marker=f'dashboard-choice-{first}').click()
        user.find(marker='dashboard-apply').click()
        await settled(page)
        assert set(board.plots) == {first}
        user.find(marker=f'board-size-{first}').trigger('resize', {'width': 680, 'height': 430})
        user.find(marker=f'board-plot-{first}').trigger('plotly_relayout', {'title.text': 'renamed on dashboard'})
        user.find(marker=f'board-plot-{first}').trigger('plotly_restyle', [{'name': 'my control', 'marker.color': '#123456'}, [0]])
        assert board.checkboxes[first].text == 'renamed on dashboard'
        assert board.badges[first].visible
        assert board.titles[first].text == 'renamed on dashboard'
        with user.client: board.edited_only.set_value(True)
        assert set(board.checkboxes) == {first}
        with user.client: board.search.set_value('nothing matches this')
        assert not board.checkboxes
        with user.client: board.search.set_value('renamed')
        user.find(marker='dashboard-choose').click()
        user.find(marker=f'dashboard-choice-{first}').click()
        user.find(marker='dashboard-apply').click()
        await settled(page)
        assert not board.panels and not board.plots
        assert board.checkboxes[first].value is False
        board.edited(project, first, 'layout', {'title.text': 'stale event'})
        assert board.is_edited(first)
        user.find(marker='dashboard-choose').click()
        user.find(marker=f'dashboard-choice-{first}').click()
        user.find(marker='dashboard-apply').click()
        await settled(page)
        figure = board.plots[first].figure
        assert figure['layout']['title']['text'] == 'renamed on dashboard'
        assert figure['data'][0]['name'] == 'my control'
        assert figure['data'][0]['marker']['color'] == '#123456'
        # adding another graph keeps the existing layout
        with user.client:
            board.edited_only.set_value(False)
            board.search.set_value('')
        user.find(marker=f'board-size-{first}').trigger('resize', {'width': 680, 'height': 430})
        user.find(marker='dashboard-choose').click()
        user.find(marker=f'dashboard-choice-{second}').click()
        user.find(marker='dashboard-apply').click()
        await settled(page)
        assert board.panels[0] == {'name': first, 'width': 680, 'height': 430}
        with user.client: await page.install(Project.open(project.save(), tmp_path/'dashboard'))
        board.edited(project, first, 'layout', {'title.text': 'old project event'})
        assert board.is_edited(first)
        assert board.checkboxes[first].text == 'renamed on dashboard'
        assert board.checkboxes[first].value and board.checkboxes[second].value
        assert board.plots[first].figure['data'][0]['marker']['color'] == '#123456'
        with user.client:
            page.project.styles.pop(first)
            await page.show_graph()
        assert not board.is_edited(first)
        assert not board.badges[first].visible
        assert board.checkboxes[first].text == page.project.analyses[0]['title']
        with user.client: page.new_project()
        assert not board.checkboxes


@pytest.mark.asyncio
async def test_dashboard_dialog_batches_selection_and_cancels_without_changes(config, monkeypatch):
    import time
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        board = page.board
        assert not board.picker.value
        assert page.drawer in list(page.sources.ancestors())
        assert page.sources not in list(board.container.ancestors())
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        names = [analysis_name(a) for a in page.project.analyses[:3]]
        calls = []
        original = page.project.result
        def slow_result(name):
            calls.append(name)
            time.sleep(.02)
            return original(name)
        monkeypatch.setattr(page.project, 'result', slow_result)
        user.find(marker='dashboard-choose').click()
        boxes = [board.checkboxes[name] for name in names]
        for name in names: user.find(marker=f'dashboard-choice-{name}').click()
        await asyncio.sleep(.05)
        assert not calls
        assert not board.panels
        assert all(board.checkboxes[name] is box and not box.is_deleted for name, box in zip(names, boxes))
        with user.client: board.search.set_value('no matches')
        assert not board.checkboxes
        with user.client: board.search.set_value('')
        assert all(board.checkboxes[name].value for name in names)
        user.find(marker='dashboard-apply').click()
        await settled(page)
        assert not board.picker.value
        assert calls == names
        assert list(board.plots) == names
        assert all(plot.figure['data'] for plot in board.plots.values())
        assert board.count.text == '3 graphs'
        user.find(marker='dashboard-choose').click()
        user.find(marker=f'dashboard-choice-{names[0]}').click()
        user.find(marker='dashboard-cancel').click()
        assert [p['name'] for p in board.panels] == names
        user.find(marker='dashboard-choose').click()
        assert board.checkboxes[names[0]].value
        with user.client: page.new_project()
        assert not board.picker.value
        assert not board.checkboxes
        user.find(marker='dashboard-choose').click()
        assert user.notify.contains('Add your data folders first')


@pytest.mark.asyncio
async def test_single_graph_size_follows_filters_project_and_downloads(config, tmp_path, monkeypatch):
    import plotly.graph_objects as go
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        name = page.name
        assert not any(isinstance(e, ui.expansion) for e in page.sources.descendants())
        source_title = next(e for e in page.sources.descendants() if isinstance(e, ui.label))
        selection_title = next(e for e in page.filters.descendants() if isinstance(e, ui.label))
        assert source_title.classes == selection_title.classes
        assert isinstance(page.table_select.dialog, ui.dialog)
        assert not page.table_select.dialog.value
        old_window = next(e for e in page.graph_area.descendants() if 'single-chart-window' in e.classes)
        user.find(marker='graph-size').trigger('resize', {'width': 840, 'height': 420})
        user.find(marker='graph-size').trigger('resize', {'width': 0, 'height': 0})
        assert page.project.styles[name]['size'] == {'width': 840, 'height': 420}
        plot = next(e for e in page.graph_area.descendants() if isinstance(e, ui.plotly))
        assert plot.figure['config']['toImageButtonOptions']['width'] is None
        assert plot.figure['config']['toImageButtonOptions']['height'] is None
        with user.client:
            page.project.edit_layout(name, {'title.text': 'resized graph'})
            page.group_checkboxes['ko'].set_value(False)
        await settled(page)
        window = next(e for e in page.graph_area.descendants() if 'single-chart-window' in e.classes)
        assert window.style['width'] == '866px' and window.style['height'] == '446px'
        # old observer events must not resize the new plot
        page.resize_graph(page.project, name, old_window, {'width': 600, 'height': 600})
        assert page.project.result(name)['figure'].layout.width == 840
        with user.client: await page.download_graph('html')
        await asyncio.sleep(.02)
        html = user.download.http_responses[-1].text
        assert 'width:840px' in html and 'height:420px' in html
        assert 'resized graph' in html and '"responsive": false' in html
        images = []
        def capture_image(figure, **kwargs):
            images.append((figure.layout.width, figure.layout.height, kwargs))
            return b'image bytes'
        monkeypatch.setattr(go.Figure, 'to_image', capture_image)
        with user.client:
            await page.download_graph('png')
            await page.download_graph('svg')
        assert images == [(840, 420, {'format': 'png', 'scale': 1}), (840, 420, {'format': 'svg', 'scale': 1})]
        with user.client: await page.install(Project.open(page.project.save(), tmp_path/'resized'))
        assert page.project.result(name)['figure'].layout.width == 840
        assert page.project.result(name)['figure'].layout.height == 420


@pytest.mark.asyncio
async def test_dashboard_filter_group_batches_changes_and_syncs_with_sidebar(config, tmp_path):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        board, project = page.board, page.project
        names = [analysis_name(a) for a in project.analyses[:2]]
        with user.client:
            for name in names: board.add(name)
            page.workspace_tabs.set_value('page')
        await settled(page)
        filters = board.filters
        assert filters.groups.value == ['control', 'ko']
        assert len(filters.mice.value) == 6
        with user.client: filters.groups.set_value(['control'])
        assert len(filters.mice.value) == 3
        assert project.selection['groups'] == ['control', 'ko']
        assert all(groups(plot) == {'control', 'ko'} for plot in board.plots.values())
        assert filters.status.text == 'not applied'
        kept = filters.mice.value[:2]
        with user.client: filters.mice.set_value(kept)
        user.find(marker='dashboard-filter-apply').click()
        await settled(page)
        assert project.selection['groups'] == ['control']
        assert len(project.selection['exclude_mice']) == 1
        assert not page.group_checkboxes['ko'].value
        assert sum(bool(b.value) for b in page.mouse_checkboxes.values()) == 2
        assert all(groups(plot) == {'control'} for plot in board.plots.values())
        assert not filters.status.text
        assert filters.mouse_summary.text == '2 of 3 selected'
        with user.client:
            page.group_checkboxes['ko'].set_value(True)
        await settled(page)
        assert filters.groups.value == ['control', 'ko']
        assert filters.mouse_summary.text == '5 of 6 selected'
        user.find(marker='dashboard-filter-reset').click()
        assert len(project.selection['exclude_mice']) == 1
        assert len(filters.mice.value) == 6
        user.find(marker='dashboard-filter-apply').click()
        await settled(page)
        assert project.selection['exclude_mice'] == []
        with user.client: filters.mice.set_value([])
        user.find(marker='dashboard-filter-apply').click()
        await settled(page)
        assert len(project.selection['exclude_mice']) == 6
        assert all(not plot.figure['data'] for plot in board.plots.values())
        with user.client: filters.groups.set_value([])
        user.find(marker='dashboard-filter-apply').click()
        await settled(page)
        assert project.selection['groups'] == []
        assert filters.group_summary.text == 'none selected'
        user.find(marker='dashboard-filter-reset').click()
        user.find(marker='dashboard-filter-apply').click()
        await settled(page)
        with user.client: await page.install(Project.open(project.save(), tmp_path/'filters'))
        assert filters.groups.value == ['control', 'ko']
        assert len(filters.mice.value) == 6
        with user.client: page.new_project()
        assert not filters.groups.options and not filters.mice.options
        assert not filters.apply_button.enabled


@pytest.mark.asyncio
async def test_dashboard_phase_filter_updates_available_mice(config):
    config['datasets'][1]['phase'] = 'phase 2'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client: page.workspace_tabs.set_value('page')
        await settled(page)
        filters = page.board.filters
        assert filters.phase.options == ['phase 1', 'phase 2']
        with user.client: filters.phase.set_value('phase 2')
        assert {m['group'] for m in filters.mouse_records.values()} == {'ko'}
        assert page.project.selection['phases'] == ['phase 1']
        user.find(marker='dashboard-filter-apply').click()
        await settled(page)
        assert page.phase_select.value == 'phase 2'
        assert page.project.selection['phases'] == ['phase 2']


@pytest.mark.asyncio
async def test_graphs_uses_dashboard_card_resize_without_changing_controls(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        elements = list(page.graph_area.descendants())
        frame = next(e for e in elements if 'single-chart-window' in e.classes)
        assert isinstance(frame, ui.card) and 'chart-window' in frame.classes
        assert not any(isinstance(e, ui.number) for e in elements)
        assert not any('graph-resize-handle' in e.classes for e in elements)
        labels = [e.text for e in elements if isinstance(e, ui.label)]
        buttons = [e.text for e in elements if isinstance(e, ui.button)]
        assert 'apply size' not in buttons
        for width, height in [(750, 450), (720, 600), (840, 420)]:
            user.find(marker='graph-size').trigger('resize', {'width': width, 'height': height})
            figure = page.project.result(page.name)['figure']
            assert (figure.layout.width, figure.layout.height) == (width, height)
        assert list(page.graph_area.descendants()) == elements
        assert [e.text for e in elements if isinstance(e, ui.label)] == labels
        assert [e.text for e in elements if isinstance(e, ui.button)] == buttons
        with user.client: await page.download_graph('html')
        await asyncio.sleep(.02)
        assert 'width:840px' in user.download.http_responses[-1].text
        assert 'height:420px' in user.download.http_responses[-1].text


@pytest.mark.asyncio
async def test_download_keeps_frame_size_and_editor_controls_all_layout_changes(config, monkeypatch):
    import plotly.graph_objects as go
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        name = page.name
        frame = next(e for e in page.graph_area.descendants() if 'single-chart-window' in e.classes)
        user.find(marker='graph-size').trigger('resize', {'width': 840, 'height': 420})
        assert frame.style['width'] == '866px' and frame.style['height'] == '446px'
        plot = next(e for e in page.graph_area.descendants() if isinstance(e, ui.plotly))
        assert not plot.figure['config']['editable']
        assert not any(plot.figure['config']['edits'].values())
        monkeypatch.setattr(go.Figure, 'to_image', lambda *args, **kwargs: b'png')
        with user.client: await page.download_graph('png')
        assert frame is next(e for e in page.graph_area.descendants() if 'single-chart-window' in e.classes)
        assert frame.style['width'] == '866px' and frame.style['height'] == '446px'
        fields = {e.label: e for e in page.editor_area.descendants() if isinstance(e, ui.number)}
        with user.client:
            fields['title · x'].set_value(.5)
            fields['title · y'].set_value(.98)
            fields['title font · px'].set_value(24)
            fields['legend · y'].set_value(-.4)
        user.find(marker='apply-positions').click()
        await settled(page)
        layout = page.project.result(name)['figure'].layout
        assert layout.title.x == .5 and layout.title.y == .98
        assert layout.title.font.size == 24 and layout.legend.y == -.4
        assert (layout.width, layout.height) == (840, 420)
        with user.client:
            ratio = next(e for e in page.editor_area.descendants() if isinstance(e, ui.input) and e.label == 'aspect ratio · width:height')
            ratio.set_value('4:3')
        user.find(marker='apply-ratio').click()
        assert page.project.styles[name]['size'] == {'width': 840, 'height': 630}
        with user.client: ratio.set_value('bad')
        user.find(marker='apply-ratio').click()
        assert user.notify.contains('positive width:height')
        with user.client: ratio.set_value('1:0')
        user.find(marker='apply-ratio').click()
        assert page.project.styles[name]['size']['height'] == 630
        user.find(kind=ui.button, content='reset graph edits').click()
        await settled(page)
        assert page.project.styles[name] == {'size': {'width': 840, 'height': 630}}


@pytest.mark.asyncio
async def test_dashboard_groups_graphs_and_exposes_common_and_specific_settings(config):
    config['analyses'] = 'all'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        project, board = page.project, page.board
        chosen = []
        for table in ['mouse_summary', 'activity_periods', 'latency', 'learning_by_day', 'gate_sessions']:
            analysis = next(a for a in project.analyses if a['table'] == table)
            chosen.append(analysis_name(analysis))
        with user.client:
            for name in chosen: board.add(name)
            page.workspace_tabs.set_value('page')
        await settled(page)
        assert len(board.group_rows) == 5
        for name, card in board.cards.items():
            analysis = next(a for a in project.analyses if analysis_name(a) == name)
            key = (page.view_for(analysis), analysis.get('section', 'Analysis'))
            assert card.parent_slot.parent is board.group_rows[key]
        assert board.settings.area.id < board.container.id
        assert len(board.settings.periods) == 1
        periods = next(iter(board.settings.periods.values()))
        first = periods.inputs[0]
        with user.client:
            first[1].set_value('10:00')
            first[2].set_value('16:00')
            periods.add_range(first[0], {'date_from': '2024-01-01', 'date_to': '2024-01-02', 'start': 540, 'end': 900})
        with user.client: await board.settings.apply(periods.values())
        assert project.cfg['settings']['period_windows'][0]['start'] == '10:00'
        assert project.cfg['settings']['period_windows'][0]['ranges'][0]['start'] == '09:00'
        assert page.window_inputs[0][1].value == '10:00'
        fields = next(fields for fields in board.settings.local.values() if 'latency_window_min' in fields)
        with user.client: fields['latency_window_min'].set_value(30)
        with user.client: await board.settings.apply({'latency_window_min': fields['latency_window_min'].value})
        assert project.cfg['settings']['latency_window_min'] == 30
        assert page.latency.value == 30
        with user.client: board.settings.statistics['test'].set_value('mann_whitney')
        user.find(marker='dashboard-statistics-apply').click()
        await settled(page)
        assert project.cfg['settings']['test'] == 'mann_whitney'
        assert page.test.value == 'mann_whitney'
        name = chosen[0]
        card = board.cards[name]
        with user.client: board.resized(name, {'width': 700, 'height': 450})
        assert card.style['width'] == '700px' and card.style['height'] == '450px'
        with user.client: await page.show_graph()
        with user.client: board.resized(name, {'width': 400, 'height': 400}, card)
        assert board.cards[name].style['width'] == '700px'


@pytest.mark.asyncio
async def test_inference_settings_are_shared_between_graphs_and_dashboard(config):
    config['analyses'] = 'all'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client:
            for key, value in {'error': 'ci95', 'significance': 'both', 'show_ns': True,
                               'show_n': False, 'family_scope': 'section'}.items():
                page.statistics[key].set_value(value)
        user.find(marker='recalculate').click()
        await settled(page)
        settings = page.project.cfg['settings']
        assert settings['error'] == 'ci95'
        assert settings['significance'] == 'both'
        assert settings['family_scope'] == 'section'
        assert not settings['show_n']
        with user.client:
            period = next(a for a in page.project.analyses if a.get('plot') == 'period_timecourse')
            name = analysis_name(period)
            page.board.add(name)
            page.workspace_tabs.set_value('page')
        await settled(page)
        controls = page.board.settings.statistics
        assert controls['error'].value == 'ci95'
        assert controls['show_ns'].value is True
        assert controls['show_n'].value is False
        with user.client:
            editor = next(iter(page.board.settings.periods.values()))
            editor.style.set_value('paired')
            await page.board.settings.apply(editor.values())
        assert page.project.cfg['settings']['period_style'] == 'paired'
        assert page.period_editor.style.value == 'paired'
        result = page.project.result(name)
        assert any(trace.meta['role'] == 'paired mice' for trace in result['figure'].data)
        assert all(note.name != 'sample-size' for note in result['figure'].layout.annotations)


@pytest.mark.asyncio
@pytest.mark.parametrize('location', ['graphs', 'dashboard'])
async def test_apply_waits_for_background_graph_instead_of_losing_settings(config, monkeypatch, location):
    from threading import Event
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        started, release = Event(), Event()
        original = page.project.result
        first = True
        def slow(name):
            nonlocal first
            result = original(name)
            if first:
                first = False
                started.set()
                assert release.wait(5)
            return result
        monkeypatch.setattr(page.project, 'result', slow)
        with user.client:
            rendering = asyncio.create_task(page.show_graph())
            for _ in range(200):
                if started.is_set(): break
                await asyncio.sleep(.01)
            assert started.is_set()
            if location == 'graphs':
                page.error.set_value('sd')
                applying = asyncio.create_task(page.recalculate())
            else:
                applying = asyncio.create_task(page.board.settings.apply({'error': 'sd'}))
            try:
                await asyncio.sleep(.02)
                assert not applying.done()
                assert page.project.cfg['settings']['error'] == 'sem'
            finally:
                release.set()
            await rendering
            await applying
        assert page.project.cfg['settings']['error'] == 'sd'
        assert page.error.value == 'sd'
