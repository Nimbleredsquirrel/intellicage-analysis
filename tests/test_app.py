import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from nicegui import ui, app
from fastapi.routing import APIRoute
from nicegui.testing.user_simulation import user_simulation

from intellicage.interface import Explorer, import_folder
from intellicage.project import Project
from intellicage.catalogue import analysis_name


async def wait_loaded(page):
    for _ in range(100):
        if not page.busy and any(page.graph_area.descendants()):
            return
        await asyncio.sleep(.05)
    raise AssertionError('Graph did not load')


@pytest.mark.asyncio
async def test_import_requires_user_labels_and_preserves_original_files(config, tmp_path):
    import hashlib
    folder = Path(config['datasets'][0]['path'])
    (folder / 'intellicage.json').unlink()
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
    pages = []
    async def root(): pages.append(Explorer(None, local=True, native=True))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client:
            await page.add_folders([folder])
        assert page.project is None
        assert page.import_dialog.value
        assert page.import_label_inputs[0][2].value == ''
        with user.client:
            await page.confirm_import()
        assert user.notify.contains('Group and phase names are required')
        assert page.import_dialog.value
        with user.client:
            group, lot, phase = page.import_label_inputs[0]
            group.set_value(' control ')
            lot.set_value('lot A')
            phase.set_value('phase 3')
            await page.confirm_import()
        assert not page.import_dialog.value
        assert page.project.groups == ['control']
        assert page.project.phases == ['phase 3']
        restored = Project.open(page.project.save(), tmp_path/'reopened')
        assert restored.datasets[0].lot == 'lot A'
        assert restored.phases == ['phase 3']
        assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
        with user.client:
            await page.remove_folder(folder)
            await page.add_folders([folder])
        assert page.project.phases == ['phase 3']
        assert not page.import_dialog.value


@pytest.mark.asyncio
async def test_cancel_import_and_reopen_labels(config):
    folder = Path(config['datasets'][0]['path'])
    (folder / 'intellicage.json').unlink()
    pages = []
    async def root(): pages.append(Explorer(None, local=True, native=True))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client: await page.add_folders([folder])
        user.find(marker='cancel-import').click()
        assert page.project is None
        assert not page.import_dialog.value
        with user.client: await page.add_folders([folder])
        assert page.import_dialog.value
        with user.client: page.new_project()
        assert not page.import_dialog.value


@pytest.mark.asyncio
async def test_multiple_unlabelled_folders_validate_together_before_install(config):
    folders = [Path(ds['path']) for ds in config['datasets']]
    for folder in folders: (folder / 'intellicage.json').unlink()
    pages = []
    async def root(): pages.append(Explorer(None, local=True, native=True))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client:
            await page.add_folders(folders)
            for group, lot, phase in page.import_label_inputs:
                group.set_value('control')
                phase.set_value('phase 3')
            await page.confirm_import()
        assert page.project is None
        assert page.import_dialog.value
        assert user.notify.contains('Each export needs its own')
        with user.client:
            page.import_label_inputs[1][1].set_value('second lot')
            await page.confirm_import()
        assert len(page.project.datasets) == 2
        assert page.project.phases == ['phase 3']
        assert not page.import_dialog.value


@pytest.mark.asyncio
async def test_settings_only_start_has_no_preloaded_experiments(tmp_path):
    import yaml
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump({'datasets': [], 'settings': {'error': 'sd'}}))
    pages = []
    async def root(): pages.append(Explorer(config, local=True, native=True))
    async with user_simulation(root) as user:
        await user.open('/')
        await asyncio.sleep(.3)
        assert pages[0].project is None
        assert pages[0].defaults['settings']['error'] == 'sd'
        assert not user.notify.messages


@pytest.mark.asyncio
async def test_dashboard_filters_edits_and_downloads(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=False)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        app.router.routes.insert(0, APIRoute('/imports/{token}', import_folder, methods=['POST']))
        await user.open('/')
        page = pages[0]
        await wait_loaded(page)
        await user.should_see(marker='plot')
        with user.client:
            page.group_checkboxes['ko'].set_value(False)
            page.mouse_checkboxes[('control', 'lot 1', 1)].set_value(False)
        await asyncio.sleep(.2)
        result = page.project.result(page.name)
        assert set(result['data']['group']) == {'control'}
        assert 1 not in result['data']['animal']
        user.find(marker='chart-title').clear().type('my visits')
        user.find(marker='apply-titles').click()
        await asyncio.sleep(.3)
        assert page.project.result(page.name)['figure'].layout.title.text == 'my visits'
        user.find(marker='prism-csv').click()
        await asyncio.sleep(.1)
        with user.client:
            await page.save_project()
            await page.download_graph('html')
        await asyncio.sleep(.1)
        assert user.download.http_responses


@pytest.mark.asyncio
async def test_empty_start_and_folder_import(config):
    pages = []
    async def root():
        page = Explorer(None, local=False, native=False)
        pages.append(page)
    async with user_simulation(root) as user:
        app.router.routes.insert(0, APIRoute('/imports/{token}', import_folder, methods=['POST']))
        await user.open('/')
        await user.should_see('Load your exports to begin.')
        page = pages[0]
        token = page.token
        response = await user.http_client.post(f'/imports/{token}', files=[
            ('files', (f'export/{p.name}', p.read_bytes()))
            for p in Path(config['datasets'][0]['path']).iterdir() if p.is_file()])
        assert response.status_code == 200, response.text
        with user.client:
            await page.load_pending()
        assert page.project is not None, str(user.notify.messages)
        assert page.project.datasets[0].visits.height == 12
        response = await user.http_client.post('/imports/missing', files={'files': ('../bad.txt', b'x')})
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_settings_labels_colors_and_native_folder(config, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from nicegui import app
    import webview
    pages=[]
    async def root():
        page=Explorer(None,local=True,native=True);pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/');page=pages[0];await wait_loaded(page)
        with user.client:
            page.window_inputs[0][1].set_value('10:00')
            page.window_inputs[0][2].set_value('16:00')
            page.window_inputs[1][1].set_value('22:00')
            page.window_inputs[1][2].set_value('04:00')
            await page.recalculate()
        assert page.project.cfg['settings']['period_windows'][0]['start']=='10:00'
        assert page.latency.value is None
        with user.client:
            page.latency.set_value(5)
            await page.recalculate()
        assert page.project.cfg['settings']['latency_window_min'] == 5
        assert page.project.tables['latency']['latency_first_visit_censored'].all()
        with user.client:
            page.latency.set_value(None)
            await page.recalculate()
        assert page.project.cfg['settings']['latency_window_min'] is None
        assert not page.project.tables['latency']['latency_first_visit_censored'].any()
        user.find(kind=ui.input,content='legend · control').clear().type('new control')
        # the editor has one apply button per group
        user.find(marker='apply-color-control').click()
        await asyncio.sleep(.2)
        assert page.project.result(page.name)['figure'].data[0].name == 'new control'
        with user.client:
            await page.download_graph('html')
            await page.add_folders([tmp_path/'missing'])
        assert user.notify.contains('Folder not found')
        # folder picker cancellation is harmless
        monkeypatch.setattr(app.native,'main_window',SimpleNamespace(create_file_dialog=AsyncMock(return_value=None), signal_server_shutdown=lambda:None))
        with user.client: await page.native_folder()
        with user.client:
            page.edit_labels(0)
            page.label_inputs[0].set_value('renamed')
            await page.relabel()
        assert page.project.datasets[0].group=='renamed'
        with user.client:
            page.edit_labels(0)
            page.label_inputs[0].set_value('')
            await page.relabel()
        assert user.notify.contains('names are required')
        with user.client:
            page.metadata_dialog.close()
            for box in page.group_checkboxes.values(): box.set_value(False)
        await asyncio.sleep(.2)
        assert page.project.result(page.name)['data'].is_empty()


@pytest.mark.asyncio
async def test_upload_zip_project_and_empty_selection(config,tmp_path):
    import io
    import zipfile
    from types import SimpleNamespace
    from pathlib import Path
    pages=[]
    async def root(): pages.append(Explorer(None,local=True,native=False))
    async with user_simulation(root) as user:
        await user.open('/');page=pages[0]
        class File:
            def __init__(self,name,data): self.name,self.data=name,data
            async def read(self): return self.data
        packed=io.BytesIO()
        source=Path(config['datasets'][0]['path'])
        with zipfile.ZipFile(packed,'w') as z:
            for path in source.rglob('*.txt'):z.write(path,f'control/{path.relative_to(source)}')
            z.write(source/'intellicage.json', 'control/intellicage.json')
        with user.client:
            await page.upload(SimpleNamespace(file=File('export.zip',packed.getvalue())))
        await wait_loaded(page)
        assert page.project.datasets[0].visits.height==12
        saved=page.project.save()
        with user.client:
            await page.upload(SimpleNamespace(file=File('saved.icage',saved)))
        await asyncio.sleep(.2)
        assert page.project.datasets[0].group=='control'
        with user.client:
            await page.set_mice(False)
        assert page.project.result(page.name)['data'].is_empty()
        assert page.project.result(page.name)['notes']
        with user.client: await page.export_all()
        await asyncio.sleep(.1)
        assert user.download.http_responses
        with user.client:
            await page.load_pending()
            await page.add_folders([source])
        # importing the same path has its own validation message
        assert page.project is not None
        user.find(marker='new-project').click()
        assert page.project is None


@pytest.mark.asyncio
async def test_initial_config_error_handling_busy_and_no_project(config,tmp_path):
    import yaml
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    pages=[]
    async def root():pages.append(Explorer(path,local=True,native=False))
    async with user_simulation(root) as user:
        await user.open('/');page=pages[0]
        for _ in range(100):
            if page.project:break
            await asyncio.sleep(.05)
        await wait_loaded(page)
        with user.client:
            page.busy=True
            assert await page.task(lambda:1) is None
            await page.show_graph()
            page.busy=False
            assert await page.task(lambda: (_ for _ in ()).throw(ValueError('bad data'))) is None
        assert user.notify.contains('bad data')
        with user.client:
            page.project=None
            await page.save_project();await page.export_all();await page.download_graph('html')
            await page.show_graph();page.choose_section()


@pytest.mark.asyncio
async def test_plot_events_and_csv_buttons(config):
    pages=[]
    async def root():
        page=Explorer(None,local=False,native=False);pages.append(page);await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/');page=pages[0];await wait_loaded(page)
        user.find(marker='plot').trigger('plotly_relayout',{'title.text':'direct edit'})
        user.find(marker='plot').trigger('plotly_restyle',{'0':{'name':['direct legend']},'1':[0]})
        assert page.project.result(page.name)['figure'].layout.title.text=='direct edit'
        assert page.project.result(page.name)['figure'].data[0].name=='direct legend'
        user.find(kind=ui.button,content='reset graph edits').click()
        await asyncio.sleep(.2)
        assert page.name not in page.project.styles
        # exercise each saved graph type through the graph chooser
        for analysis in page.project.analyses:
            with user.client:
                page.graph_select.set_value(analysis.get('name') or analysis_name(analysis))
            await asyncio.sleep(.15)
        assert page.graph_area.descendants()


def test_app_launch_modes_and_missing_local_datasets(tmp_path, monkeypatch):
    import yaml
    import intellicage.interface as interface
    calls = []
    monkeypatch.setattr(ui, 'run', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(interface.native, 'find_open_port', lambda: 9123)
    interface.main(['--no-show', '--config', str(tmp_path/'absent')])
    assert calls[-1]['host'] == '127.0.0.1'
    assert calls[-1]['title'] == 'intellicage analysis'
    assert calls[-1]['native']
    assert calls[-1]['port'] == 9123
    assert app.native.settings['ALLOW_DOWNLOADS']
    interface.main(['--desktop', '--port', '9000', '--config', str(tmp_path/'absent')])
    assert calls[-1]['native'] and calls[-1]['port'] == 9000
    interface.main(['--browser', '--config', str(tmp_path/'absent')])
    assert not calls[-1]['native']
    assert calls[-1]['host'] == '127.0.0.1'
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump({'datasets': [{'path': 'absent'}]}))
    interface.main(['--config', str(path)])
    assert calls[-1]['native']
    with pytest.raises(SystemExit): interface.main(['--desktop', '--browser'])
    with pytest.raises(SystemExit): interface.main(['--web'])


@pytest.mark.asyncio
async def test_folder_upload_rejects_unsafe_paths_and_limits(config,monkeypatch):
    import intellicage.interface as interface
    pages=[]
    async def root():pages.append(Explorer(None,local=False,native=False))
    async with user_simulation(root) as user:
        app.router.routes.insert(0,APIRoute('/imports/{token}',import_folder,methods=['POST']))
        await user.open('/');page=pages[0]
        path=f'/imports/{page.token}'
        for files in [[('files',('../escape.txt',b'bad'))],
                      [('files',('a.txt',b'x')),('files',('a.txt',b'y'))],
                      [('files',('unrelated.csv',b'x'))]]:
            response=await user.http_client.post(path,files=files)
            assert response.status_code==400
        monkeypatch.setattr(interface,'MAX_UPLOAD',1)
        response=await user.http_client.post(path,data={'note':'a'},files={'files':('large.txt',b'xx')})
        assert response.status_code==400
        assert '1 GB' in response.json()['detail']


@pytest.mark.asyncio
async def test_invalid_initial_configuration_stays_in_empty_app(tmp_path):
    path=tmp_path/'bad.yaml';path.write_text('datasets: [invalid')
    pages=[]
    async def root():pages.append(Explorer(path,local=False,native=False))
    async with user_simulation(root) as user:
        await user.open('/')
        await asyncio.sleep(.3)
        assert pages[0].project is None
        assert user.notify.messages


@pytest.mark.asyncio
async def test_activity_sections_default_windows_and_custom_day_night(config):
    config['analyses']='all'
    pages=[]
    async def root():
        page=Explorer(None,local=True,native=False);pages.append(page);await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/');page=pages[0];await wait_loaded(page)
        assert [(start.value,end.value) for _,start,end in page.window_inputs]==[('08:00','20:00'),('20:00','08:00')]
        with user.client:
            page.section.set_value('Day / night')
        await asyncio.sleep(.3)
        assert len(page.graph_select.options)==6
        assert len(page.project.result(page.name)['figure'].data)==4
        with user.client:
            page.window_inputs[0][1].set_value('10:00')
            page.window_inputs[0][2].set_value('16:00')
            page.window_inputs[1][1].set_value('22:00')
            page.window_inputs[1][2].set_value('04:00')
            await page.recalculate()
        result=page.project.result(page.name)
        assert result['data']['hours'].unique().to_list()==[6.]
        assert len(result['figure'].data)==4
        with user.client:
            page.section.set_value('Hourly activity')
        await asyncio.sleep(.3)
        assert len(page.graph_select.options)==6
        assert not page.project.result(page.name)['figure'].layout.shapes


@pytest.mark.asyncio
async def test_table_previews_are_built_only_when_opened(config):
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        await wait_loaded(page)
        assert not any(isinstance(e, ui.aggrid) for e in page.details_area.descendants())
        with user.client:
            statistics = next(e for e in page.details_area.descendants()
                              if isinstance(e, ui.expansion) and e.text == 'statistics & graph data')
            statistics.set_value(True)
        await asyncio.sleep(.1)
        count = sum(isinstance(e, ui.aggrid) for e in page.details_area.descendants())
        assert count > 0
        with user.client:
            statistics.set_value(False)
            statistics.set_value(True)
            intermediate = next(e for e in page.details_area.descendants()
                                if isinstance(e, ui.expansion) and e.text == 'all intermediate tables')
            intermediate.set_value(True)
        await asyncio.sleep(.1)
        assert sum(isinstance(e, ui.aggrid) for e in page.details_area.descendants()) == count
        assert any(isinstance(e, ui.button) and 'mouse_summary' in e.text
                   for e in intermediate.descendants())
        with user.client:
            intermediate.set_value(False)
            intermediate.set_value(True)
        await asyncio.sleep(.1)
        assert sum(isinstance(e, ui.button) for e in intermediate.descendants()) == len(page.project.tables)


@pytest.mark.asyncio
async def test_long_operation_shows_status_and_restores_controls():
    from threading import Event
    pages = []
    release = Event()
    async def root():
        pages.append(Explorer(None, local=True, native=True))
    def slow_load():
        assert release.wait(5)
        return 'loaded'
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        with user.client:
            pending = asyncio.create_task(page.task(slow_load, status='reading exports…'))
            try:
                await asyncio.sleep(.05)
                assert page.busy
                assert page.status.text == 'reading exports…'
                assert all(not control.enabled for control in page.task_controls)
            finally:
                release.set()
                assert await pending == 'loaded'
        assert not page.busy
        assert all(control.enabled for control in page.task_controls)
        assert page.status.text.startswith('done · ')


@pytest.mark.asyncio
async def test_plus_adds_multiple_folders_and_skips_overlapping_exports(config, monkeypatch, tmp_path):
    from types import SimpleNamespace
    import webview
    pages = []
    paths = [ds['path'] for ds in config['datasets']]
    dialog = AsyncMock(return_value=paths)
    monkeypatch.setattr(app.native, 'main_window', SimpleNamespace(
        create_file_dialog=dialog, signal_server_shutdown=lambda: None))
    async def root():
        pages.append(Explorer(None, local=True, native=True))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        assert not hasattr(page, 'path_input')
        assert not any(isinstance(e, ui.upload) for e in user.client.content.descendants())
        assert not any(isinstance(e, ui.expansion) and e.text == 'how the numbers work'
                       for e in user.client.content.descendants())
        assert not any(isinstance(e, ui.label) and 'mouse by mouse' in str(e.text)
                       for e in user.client.content.descendants())
        user.find(marker='add-folders').click()
        await wait_loaded(page)
        dialog.assert_awaited_once_with(webview.FileDialog.FOLDER, allow_multiple=True)
        assert {ds.path.resolve() for ds in page.project.datasets} == {Path(p).resolve() for p in paths}
        assert len(page.project.datasets) == 2
        assert len({ds.lot for ds in page.project.datasets}) == 2
        before = page.project
        # a parent and its child must not import the same export twice
        with user.client:
            await page.add_folders([Path(paths[0]).parent, paths[0]])
        assert page.project is before
        assert user.notify.contains('already in the project')
        # invalid selections leave the previous project intact
        with user.client:
            await page.add_folders([paths[0], tmp_path/'missing'])
        assert page.project is before
        assert user.notify.contains('Folder not found')
        dialog.return_value = None
        with user.client:
            await page.native_folder()
            page.busy = True
            await page.native_folder()
            page.busy = False
        assert dialog.await_count == 2
        assert page.project is before


@pytest.mark.asyncio
async def test_native_open_project_restores_file_and_handles_cancel(config, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import webview
    original = Project(config)
    original.set_selection({'groups': ['control'], 'phases': ['phase 1'], 'exclude_mice': []})
    path = tmp_path/'saved.icage'
    path.write_bytes(original.save())
    dialog = AsyncMock(return_value=[str(path)])
    monkeypatch.setattr(app.native, 'main_window', SimpleNamespace(
        create_file_dialog=dialog, signal_server_shutdown=lambda: None))
    pages = []
    async def root():
        pages.append(Explorer(None, local=True, native=True))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        user.find(marker='open-project').click()
        await wait_loaded(page)
        dialog.assert_awaited_once_with(webview.FileDialog.OPEN, file_types=('IntelliCage project (*.icage)',))
        assert page.project.selection == original.selection
        assert page.project.datasets[0].visits.equals(original.datasets[0].visits)
        before = page.project
        dialog.return_value = None
        with user.client:
            await page.native_project()
            page.busy = True
            await page.native_project()
            page.busy = False
        assert dialog.await_count == 2
        assert page.project is before


@pytest.mark.asyncio
async def test_remove_folders_updates_results_and_keeps_source_files(config, tmp_path):
    import hashlib
    pages = []
    source = Path(config['datasets'][0]['path'])
    original = hashlib.sha256((source/'Visit.txt').read_bytes()).hexdigest()
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        await wait_loaded(page)
        page.project.set_selection({'groups': ['control'], 'phases': ['phase 1'], 'exclude_mice': [
            {'group': 'control', 'lot': 'lot 1', 'animal': 1},
            {'group': 'ko', 'lot': 'lot 2', 'animal': 2}]})
        user.find(marker='remove-folder-0').click()
        for _ in range(100):
            if len(page.project.datasets) == 1 and not page.busy: break
            await asyncio.sleep(.05)
        await wait_loaded(page)
        assert len(page.project.datasets) == 1
        assert page.project.groups == ['ko']
        assert page.project.selection['groups'] == ['ko']
        assert page.project.selection['exclude_mice'] == [{'group': 'ko', 'lot': 'lot 2', 'animal': 2}]
        result = page.project.result(page.name)
        assert set(result['data']['group']) == {'ko'}
        assert 2 not in result['data']['animal']
        assert result['figure'].data[0].marker.color == '#eb6834'
        assert hashlib.sha256((source/'Visit.txt').read_bytes()).hexdigest() == original
        reopened = Project.open(page.project.save(), tmp_path/'saved')
        assert len(reopened.datasets) == 1 and reopened.groups == ['ko']
        # stale clicks and clicks during a calculation must not remove anything
        before = page.project
        with user.client:
            await page.remove_folder(source)
            page.busy = True
            await page.remove_folder(page.project.datasets[0].path)
            page.busy = False
        assert page.project is before
        last = page.project.datasets[0].path
        with user.client:
            await page.remove_folder(last)
        assert page.project is None
        assert not any(page.graph_area.descendants())
        assert not any(page.filters.descendants())
        assert len(page.folder_remove_buttons) == 0
        assert (last/'Visit.txt').is_file()
        assert any(isinstance(e, ui.label) and e.text == 'no folders added'
                   for e in page.folder_list.descendants())
        with user.client:
            await page.remove_folder(last)


@pytest.mark.asyncio
async def test_failed_folder_removal_preserves_current_project(config, monkeypatch):
    import intellicage.interface as interface
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        await wait_loaded(page)
        before = page.project
        def fail(_):
            raise ValueError('could not rebuild')
        monkeypatch.setattr(interface, 'Project', fail)
        with user.client:
            await page.remove_folder(page.project.datasets[0].path)
        assert page.project is before
        assert len(page.folder_remove_buttons) == 2
        assert all(button.enabled for button in page.folder_remove_buttons)
        assert user.notify.contains('could not rebuild')


def visible_plot(page):
    return next(e for e in page.graph_area.descendants() if isinstance(e, ui.plotly)).figure


def plotted_groups(page):
    return {trace['meta']['group'] for trace in visible_plot(page)['data']}


@pytest.mark.asyncio
@pytest.mark.parametrize('metric', ['visits_n', 'visits_duration_total_s'])
async def test_folder_changes_refresh_the_visible_chart_without_changing_page(config, metric):
    config['analyses'] = 'all'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        graph = next(a['name'] for a in page.project.analyses if a.get('table') == 'mouse_summary' and a.get('value') == metric)
        with user.client:
            page.graph_select.set_value(graph)
            await page.show_graph()
            page.project.set_selection({'groups': ['control', 'ko'], 'phases': ['phase 1'],
                                        'exclude_mice': [{'group': 'ko', 'lot': 'lot 2', 'animal': 2}]})
            page.project.edit_layout(graph, {'title.text': 'my graph'})
            await page.show_graph()
        assert plotted_groups(page) == {'control', 'ko'}
        original_p = page.project.result(graph)['stats']['test']['p'][0]
        # cache another chart before changing the source data
        other = next(a['name'] for a in page.project.analyses if a.get('value') == 'licks_n' and a['table'] == 'mouse_summary')
        page.project.result(other)
        removed = page.project.datasets[0]
        with user.client:
            await page.remove_folder(removed.path)
        assert page.name == graph and page.section.value == 'Visits'
        assert plotted_groups(page) == {'ko'}
        assert visible_plot(page)['layout']['title']['text'] == 'my graph'
        assert set(page.project.result(other)['data']['group']) == {'ko'}
        with user.client:
            await page.add_folders([removed.path])
        assert page.name == graph and page.section.value == 'Visits'
        assert plotted_groups(page) == {'control', 'ko'}
        assert visible_plot(page)['layout']['title']['text'] == 'my graph'
        restored = next(ds for ds in page.project.datasets if ds.path == removed.path)
        assert (restored.group, restored.lot, restored.phase) == (removed.group, removed.lot, removed.phase)
        assert set(page.project.result(other)['data']['group']) == {'control', 'ko'}
        assert page.project.groups == ['control', 'ko']
        assert page.project.result(graph)['stats']['test']['p'][0] == pytest.approx(original_p)
        assert page.project.selection['exclude_mice'] == [{'group': 'ko', 'lot': 'lot 2', 'animal': 2}]
        ko_points = next(t for t in visible_plot(page)['data'] if t['meta'] == {'group': 'ko', 'role': 'mice', 'period': ''})
        assert len(ko_points['customdata']) == 2
        # restoring after the last folder was removed must retain its phase too
        with user.client:
            for ds in list(page.project.datasets):
                await page.remove_folder(ds.path)
            await page.add_folders([removed.path])
        assert plotted_groups(page) == {'control'}
        assert page.project.datasets[0].phase == removed.phase


@pytest.mark.asyncio
async def test_old_render_cannot_overwrite_the_latest_graph(config, monkeypatch):
    from threading import Event
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
        started, release = Event(), Event()
        result = page.project.result
        def delayed(name):
            if name == first:
                started.set()
                assert release.wait(5)
            return result(name)
        monkeypatch.setattr(page.project, 'result', delayed)
        with user.client:
            page.name = first
            old = asyncio.create_task(page.show_graph())
            try:
                for _ in range(100):
                    if started.is_set(): break
                    await asyncio.sleep(.01)
                assert started.is_set()
                page.name = second
                latest = asyncio.create_task(page.show_graph())
                await asyncio.sleep(.01)
            finally:
                release.set()
            await asyncio.gather(old, latest)
        assert visible_plot(page)['layout']['title']['text'] == next(a['title'] for a in page.project.analyses if a['name'] == second)


@pytest.mark.asyncio
async def test_readding_original_folder_after_opening_saved_project_preserves_phase(config, tmp_path):
    config['analyses'] = 'all'
    config['datasets'][0]['phase'] = config['datasets'][1]['phase'] = 'Phase 3'
    original_path = Path(config['datasets'][0]['path'])
    saved = Project(config).save()
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project.open(saved, tmp_path/'opened'))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        removed = page.project.datasets[0]
        assert removed.path != original_path
        with user.client:
            await page.remove_folder(removed.path)
            await page.add_folders([original_path])
        assert plotted_groups(page) == {'control', 'ko'}
        assert next(ds for ds in page.project.datasets if ds.path == original_path).phase == 'Phase 3'
