import asyncio
from pathlib import Path

import pytest
from nicegui import ui, app
from fastapi.routing import APIRoute
from nicegui.testing.user_simulation import user_simulation

from intellicage.interface import Explorer, import_folder
from intellicage.project import Project
from intellicage.catalogue import analysis_name


async def wait_loaded(page):
    for _ in range(100):
        if not page.busy and page.graph_area.descendants():
            return
        await asyncio.sleep(.05)
    raise AssertionError('Graph did not load')


@pytest.mark.asyncio
async def test_dashboard_filters_edits_and_downloads(config):
    pages = []
    def root():
        page = Explorer(None, local=True, native=False)
        pages.append(page)
        page.install(Project(config))
    async with user_simulation(root) as user:
        app.router.routes.insert(0, APIRoute('/imports/{token}', import_folder, methods=['POST']))
        await user.open('/')
        page = pages[0]
        await wait_loaded(page)
        await user.should_see(marker='plot')
        with user.client:
            page.group_select.set_value(['control'])
            page.mouse_select.set_value([0])
            await page.apply_selection()
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
    def root():
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
            for p in Path(config['datasets'][0]['path']).glob('*.txt')])
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
    def root():
        page=Explorer(None,local=True,native=True);pages.append(page)
        page.install(Project(config))
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
            page.path_input.set_value(str(tmp_path/'missing'))
            await page.local_folder()
        assert user.notify.contains('Folder not found')
        # folder picker cancellation is harmless
        monkeypatch.setattr(app.native,'main_window',SimpleNamespace(create_file_dialog=lambda *_:None, signal_server_shutdown=lambda:None))
        with user.client: await page.native_folder()
        with user.client:
            page.label_inputs[0][0].set_value('renamed')
            await page.relabel()
        assert page.project.datasets[0].group=='renamed'
        with user.client:
            page.label_inputs[0][0].set_value('')
            await page.relabel()
        assert user.notify.contains('names are required')
        with user.client:
            page.group_select.set_value([])
            await page.apply_selection()
        assert user.notify.contains('Select a group')


@pytest.mark.asyncio
async def test_upload_zip_project_and_empty_selection(config,tmp_path):
    import io
    import zipfile
    from types import SimpleNamespace
    from pathlib import Path
    pages=[]
    def root(): pages.append(Explorer(None,local=True,native=False))
    async with user_simulation(root) as user:
        await user.open('/');page=pages[0]
        class File:
            def __init__(self,name,data): self.name,self.data=name,data
            async def read(self): return self.data
        packed=io.BytesIO()
        source=Path(config['datasets'][0]['path'])
        with zipfile.ZipFile(packed,'w') as z:
            for path in source.rglob('*.txt'):z.write(path,f'control/{path.relative_to(source)}')
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
            page.mouse_select.set_value(list(page.mouse_select.options))
            await page.apply_selection()
        assert page.project.result(page.name)['data'].is_empty()
        assert page.project.result(page.name)['notes']
        with user.client: await page.export_all()
        await asyncio.sleep(.1)
        assert user.download.http_responses
        with user.client:
            await page.load_pending()
            page.path_input.set_value(str(source))
            await page.local_folder()
        # importing the same path has its own validation message
        assert page.project is not None
        user.find(marker='new-project').click()
        assert page.project is None


@pytest.mark.asyncio
async def test_initial_config_error_handling_busy_and_no_project(config,tmp_path):
    import yaml
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    pages=[]
    def root():pages.append(Explorer(path,local=True,native=False))
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
    def root():
        page=Explorer(None,local=False,native=False);pages.append(page);page.install(Project(config))
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


def test_app_launch_modes_and_missing_local_datasets(tmp_path,monkeypatch):
    import yaml
    import intellicage.interface as interface
    calls=[]
    monkeypatch.setattr(ui,'run',lambda **kwargs:calls.append(kwargs))
    interface.main(['--web','--no-show','--config',str(tmp_path/'absent'),'--port','9000'])
    assert calls[-1]['host']=='0.0.0.0'
    interface.main(['--desktop','--config',str(tmp_path/'absent')])
    assert calls[-1]['native']
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump({'datasets':[{'path':'absent'}]}))
    interface.main(['--config',str(path)])
    assert calls[-1]['host']=='127.0.0.1'
    with pytest.raises(SystemExit):interface.main(['--desktop','--web'])


@pytest.mark.asyncio
async def test_folder_upload_rejects_unsafe_paths_and_limits(config,monkeypatch):
    import intellicage.interface as interface
    pages=[]
    def root():pages.append(Explorer(None,local=False,native=False))
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
    def root():pages.append(Explorer(path,local=False,native=False))
    async with user_simulation(root) as user:
        await user.open('/')
        await asyncio.sleep(.3)
        assert pages[0].project is None
        assert user.notify.messages


@pytest.mark.asyncio
async def test_activity_sections_default_windows_and_custom_day_night(config):
    config['analyses']='all'
    pages=[]
    def root():
        page=Explorer(None,local=True,native=False);pages.append(page);page.install(Project(config))
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
