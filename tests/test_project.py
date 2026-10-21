import io
import json
import zipfile

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from intellicage.catalogue import analysis_name
from intellicage.project import Project, discover, layout_patch, safe_name, unpack


@pytest.fixture
def project(config):
    config['analyses'] = 'all'
    return Project(config)


def test_full_catalogue_and_statistics(project):
    assert len(project.analyses) == 63
    assert len({a['name'] for a in project.analyses}) == len(project.analyses)
    for analysis in project.analyses:
        result = project.result(analysis_name(analysis))
        assert result['figure'].data or result['notes']
        assert result['data'].height or result['notes']
        assert len(result['data']['phase'].unique()) <= 1


def test_save_reopen_preserves_selection_inputs_and_graph_edits(project, tmp_path):
    name = project.analyses[0]['name']
    project.set_selection({'groups':['control'], 'phases':['phase 1'],
                           'exclude_mice':[{'group':'control','lot':'lot 1','animal':1}]})
    original = project.result(name)
    project.edit_layout(name, {'title.text':'my title', 'yaxis.range':[0,100], 'unknown':1, 'autosize':True})
    project.edit_traces(name, [{'name':'edited control', 'marker.color':'#123456','x':[[999]]},[0]])
    restored = Project.open(project.save(),tmp_path/'opened')
    result = restored.result(name)
    assert restored.selection == project.selection
    assert result['figure'].layout.title.text == 'my title'
    assert result['figure'].layout.yaxis.range == (0,100)
    assert result['figure'].data[0].name == 'edited control'
    assert result['figure'].data[0].marker.color == '#123456'
    assert list(result['figure'].data[0].x) == list(original['figure'].data[0].x)
    assert_frame_equal(result['data'], original['data'])
    assert result['data'].height == 2
    assert restored.datasets[0].visits.equals(project.datasets[0].visits)


def test_result_copies_figures_and_cache_is_invalidated(project):
    name=project.analyses[0]['name']
    a=project.result(name)
    a['figure'].update_layout(title_text='external mutation')
    assert project.result(name)['figure'].layout.title.text != 'external mutation'
    project.rebuild({'error':'sd'})
    assert project.cache == {}
    project.result(name)
    project.set_selection({'groups':['ko'],'phases':['phase 1']})
    assert project.cache == {}
    assert set(project.result(name)['data']['group']) == {'ko'}


def test_invalid_settings_and_selection_leave_current_project_usable(project):
    old=project.tables
    with pytest.raises(ValueError): project.rebuild({'latency_window_min':-1})
    assert project.tables is old
    with pytest.raises(ValueError): project.set_selection({'exclude_mice':[{'animal':1}]})
    assert project.selection['groups'] == project.groups


def test_export_contains_all_graph_data_stats_and_edits(project):
    name=project.analyses[0]['name']
    project.edit_layout(name, {'title.text':'saved graph'})
    with zipfile.ZipFile(io.BytesIO(project.export())) as archive:
        names=archive.namelist()
        assert len([n for n in names if n.startswith('chart_data/')]) == len(project.analyses)
        assert len([n for n in names if n.startswith('tables/')]) == 12
        figure=json.loads(archive.read(f'figures/json/{name}.json'))
        assert figure['layout']['title']['text'] == 'saved graph'
        assert 'figures/html/plotly.min.js' in names
        assert 'stats/'+name+'__test.csv' in names
        assert 'report.html' in names
        assert f'prism_tables/{name}.csv' in names
        assert f'prism_tables/{name}__subjects.csv' in names
        assert f'prism_tables/{name}__labelled.csv' in names
        layout=json.loads(archive.read(f'prism_tables/{name}.json'))
        assert layout['layout']=='column'


@pytest.mark.parametrize('name',['../evil.txt','/evil.txt','a/../../evil.txt','C:\\evil.txt',''])
def test_unsafe_paths_are_rejected(name):
    with pytest.raises(ValueError): safe_name(name)


def test_zip_traversal_and_symlinks_rejected_before_writing(tmp_path):
    for name,symlink in [('../escape.txt',False),('link.txt',True)]:
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            info=zipfile.ZipInfo(name)
            if symlink: info.external_attr=0o120777 << 16
            z.writestr(info,b'bad')
        with pytest.raises(ValueError): unpack(data.getvalue(),tmp_path/'unpacked')
    assert not (tmp_path/'escape.txt').exists()


def test_discover_and_missing_export_files(tmp_path, datasets):
    assert discover(datasets[0].path)[0]['group'] == datasets[0].group
    with pytest.raises(ValueError,match='No IntelliCage'): discover(tmp_path/'missing')
    (tmp_path/'Visit.txt').write_text('x')
    with pytest.raises(ValueError,match='missing'): discover(tmp_path)


def test_layout_edits_cannot_replace_data():
    assert layout_patch({'title.text':'ok','data[0].x':[1],'xaxis.range[0]':2,'autosize':True}) == {'title.text':'ok','xaxis.range[0]':2}


def test_plotly_zoom_reset_annotation_and_legend_events(project):
    name=project.analyses[0]['name']
    project.edit_layout(name,{'xaxis.range[0]':1,'xaxis.range[1]':9})
    assert project.result(name)['figure'].layout.xaxis.range==(1,9)
    assert not project.result(name)['figure'].layout.xaxis.autorange
    project.edit_layout(name,{'xaxis.autorange':True})
    assert project.result(name)['figure'].layout.xaxis.autorange
    assert 'xaxis.range[0]' not in project.styles[name]['layout']
    project.edit_layout(name,{'annotations[0].text':'changed p label', 'annotations[99].text':'absent'})
    assert project.result(name)['figure'].layout.annotations[0].text=='changed p label'
    project.edit_traces(name,{'0':{'visible':['legendonly'],'name':['renamed']},'1':[0]})
    assert project.result(name)['figure'].data[0].visible=='legendonly'
    project.edit_traces(name,None)
    project.edit_traces(name,[{'marker.size':[10,12]},[1,3]])
    assert project.result(name)['figure'].data[3].marker.size==12


def test_project_checksums_and_version_are_checked(project,tmp_path):
    data=project.save()
    for version,tamper in [(999,False),(1,True)]:
        output=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(output,'w') as target:
            for name in source.namelist():
                content=source.read(name)
                if name=='project.json':
                    manifest=json.loads(content);manifest['version']=version
                    content=json.dumps(manifest).encode()
                elif tamper and name.endswith('Animal.txt'): content=b'changed'
                target.writestr(name,content)
        with pytest.raises(ValueError,match='format|checksum'):
            Project.open(output.getvalue(),tmp_path/str(version))


def test_archive_limits_and_duplicate_names(tmp_path,monkeypatch):
    import intellicage.project as service
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('folder/',b'')
        archive.writestr('folder/a.txt',b'abc')
    assert unpack(data.getvalue(),tmp_path/'valid')[0].read_bytes()==b'abc'
    monkeypatch.setattr(service,'MAX_UPLOAD',1)
    with pytest.raises(ValueError,match='Upload exceeds'): unpack(data.getvalue(),tmp_path/'large')
    monkeypatch.setattr(service,'MAX_UPLOAD',10000)
    monkeypatch.setattr(service,'MAX_FILES',1)
    with pytest.raises(ValueError,match='Archive is too large'): unpack(data.getvalue(),tmp_path/'many')


def test_resized_figures_keep_dimensions_in_exports_without_changing_cache(project):
    name = project.analyses[0]['name']
    project.result(name)
    for size in [{'width': 0, 'height': 500}, {'width': True, 'height': 500},
                 {'width': float('nan'), 'height': 500}, {'width': 800},
                 {'width': 800, 'height': 4001}]:
        assert not project.resize_graph(name, size)
    assert project.resize_graph(name, {'width': 815.4, 'height': 405.6})
    assert project.cache[name]['figure'].layout.width is None
    result = project.result(name)['figure']
    assert (result.layout.width, result.layout.height, result.layout.autosize) == (815, 406, False)
    with zipfile.ZipFile(io.BytesIO(project.export())) as archive:
        figure = json.loads(archive.read(f'figures/json/{name}.json'))
        assert figure['layout']['width'] == 815
        assert figure['layout']['height'] == 406
        html = archive.read(f'figures/html/{name}.html').decode()
        assert 'width:815px' in html and 'height:406px' in html


@pytest.mark.parametrize('legacy', [True, False])
def test_ns_default_updates_old_projects_and_preserves_new_explicit_choice(config, tmp_path, legacy):
    project = Project(config)
    assert project.cfg['settings']['show_ns'] is True
    project.rebuild({'show_ns': False})
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(project.save())) as source, zipfile.ZipFile(output, 'w') as target:
        for name in source.namelist():
            content = source.read(name)
            if legacy and name == 'project.json':
                manifest = json.loads(content)
                manifest.pop('display_version')
                content = json.dumps(manifest).encode()
            target.writestr(name, content)
    restored = Project.open(output.getvalue(), tmp_path / 'restored')
    assert restored.cfg['settings']['show_ns'] is legacy
