import csv
import io
import json
from pathlib import Path
import zipfile

import polars as pl
import pytest

from intellicage.catalogue import analysis_name
from intellicage.prism import CsvTable, ReferenceTable, graph_csv, read_reference, REFERENCE_TITLES, compare_reference

FIXTURES = Path(__file__).parent / 'fixtures/prism_reference'


def reference_tables():
    return [ReferenceTable(d['title'], d['layout'], d['groups'], d['replicates'], d['row_titles'],
                           (FIXTURES/d['file']).read_text(), d['table_id'])
            for d in json.loads((FIXTURES/'index.json').read_text())]


def test_column_layout_keeps_unequal_groups_and_mouse_identity(tables):
    a={'table':'mouse_summary','value':'visits_n','title':'Visits'}
    data=tables['mouse_summary'].filter(~((pl.col('group')=='control') & (pl.col('animal')==2)))
    table=graph_csv(a,data,['ko','control'])
    rows=list(csv.reader(io.StringIO(table.content)))
    assert rows==[['4','3'],['5','5'],['6','']]
    assert table.metadata['columns']==['ko','control']
    assert table.metadata['subjects'][-1]=={'group':'ko','lot':'lot 2','animal':3,'row':3,'column':1}
    assert not table.metadata['header']


def test_grouped_layout_matches_prism_slots_and_missing_cells(tables):
    a={'table':'corner_summary','value':'time_s','by':'corner','title':'Corners'}
    data=tables['corner_summary'].filter(~((pl.col('group')=='control') & (pl.col('animal')==1) & (pl.col('corner')==2)))
    table=graph_csv(a,data,['control','ko'])
    rows=list(csv.reader(io.StringIO(table.content)))
    assert rows[0]==['Coin 1','40.0','40.0','40.0','40.0','40.0','40.0']
    assert rows[1][1]==''
    assert len(rows)==4
    assert table.metadata['replicates']==3
    assert table.metadata['subjects'][3]['column']==5


def test_period_rows_keep_day_and_night_separate(tables):
    a={'table':'activity_periods','value':'visits_n','by':'day','plot':'period_timecourse'}
    table=graph_csv(a,tables['activity_periods'],['control','ko'])
    assert table.metadata['row_levels'][0]=={'day':0,'period':'Dark'}
    assert len(table.metadata['row_levels'])==5
    assert 'J1 · Light' in table.content


def test_latency_censoring_and_gate_events_are_explicit(tables):
    table=graph_csv({'table':'latency','value':'latency_corner_4_s','plot':'latency'},tables['latency'],['control','ko'])
    assert 'right-censored' in table.metadata['censoring']
    assert table.content==',\n,\n,\n'
    gate=graph_csv({'table':'gate_sessions','plot':'gate_raster'},tables['gate_sessions'],['control','ko'])
    assert gate.metadata['layout']=='events'
    assert gate.metadata['header']
    assert gate.content.startswith('group,lot,phase,animal,')


def test_empty_and_nonfinite_values_do_not_turn_into_zero(tables):
    a={'table':'mouse_summary','value':'visits_n'}
    table=graph_csv(a,tables['mouse_summary'].head(0),['control','ko'])
    assert table.content==''
    data=tables['mouse_summary'].with_columns(pl.lit(float('nan')).alias('visits_n'))
    assert graph_csv(a,data,['control','ko']).content==',\n,\n,\n'


def test_duplicate_values_or_multiple_phases_are_rejected(tables):
    a={'table':'mouse_summary','value':'visits_n'}
    with pytest.raises(ValueError,match='one value'):
        graph_csv(a,pl.concat([tables['mouse_summary']]*2),['control','ko'])
    multiple=pl.concat([tables['mouse_summary'],tables['mouse_summary'].with_columns(pl.lit('other').alias('phase'))])
    with pytest.raises(ValueError,match='one phase'):graph_csv(a,multiple,['control','ko'])


def test_csv_writer_retains_mapping_and_reference_title(tmp_path,tables):
    table=graph_csv({'table':'mouse_summary','value':'visits_n'},tables['mouse_summary'],['control','ko'])
    table.write(tmp_path/'csv','visits')
    metadata=json.loads((tmp_path/'csv/visits.json').read_text())
    assert metadata['title']=='Nombre tot de visites'
    assert (tmp_path/'csv/visits.csv').read_text()==table.content


def make_prism(path, missing=False):
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('data/sheets/a/sheet.json',json.dumps({'title':'Visits','table':{
            'format':'column','uid':'t','dataSets':['a','b']}}))
        z.writestr('data/sets/a.json',json.dumps({'title':{'string':'control '}}))
        z.writestr('data/sets/b.json',json.dumps({'title':'ko'}))
        z.writestr('data/sheets/b/sheet.json',json.dumps({'title':'Generated:Estimation Plot','table':{'format':'column'}}))
        z.writestr('data/sheets/c/sheet.json',json.dumps({'title':'Stats','table':{'format':'view'}}))
        z.writestr('document.json','{}')
        if not missing:z.writestr('data/tables/t/data.csv','1,2\n3,4\n,\n')


def test_prism_reader_follows_metadata_and_ignores_generated_sheets(tmp_path):
    path=tmp_path/'input.prism';make_prism(path)
    tables=read_reference(path)
    assert len(tables)==1
    assert tables[0].groups==['control','ko']
    assert tables[0].group_values('control')==[['1'],['3']]
    make_prism(path,missing=True)
    with pytest.raises(ValueError,match='no embedded CSV'):read_reference(path)


@pytest.mark.real_data
@pytest.mark.parametrize('title',list(REFERENCE_TITLES.values()))
def test_every_reference_value_against_real_exports(title):
    from intellicage.pipeline import DEFAULT_SETTINGS,build_tables
    from intellicage.io import load_dataset
    root=Path.home()/'Documents'
    folders=[root/'Phase 3 Lot 5 control',root/'Phase 3 Lot 4 FMR1 KO']
    if not all(p.is_dir() for p in folders):pytest.skip('local exports not found')
    datasets=[load_dataset(p,group=g,lot=l,phase='Phase 3') for p,g,l in zip(folders,['Contrôle','FMR1 KO'],['Lot 5','Lot 4'])]
    tables=build_tables(datasets,DEFAULT_SETTINGS)
    reference=next(t for t in reference_tables() if t.title==title)
    source,metric=next(k for k,v in REFERENCE_TITLES.items() if v==title)
    factor='corner' if source=='corner_summary' else 'day' if source=='learning_by_day' else None
    actual=tables[source]
    result=compare_reference(tables,reference)
    if factor=='day':
        # this difference is visible, not hidden by a larger tolerance
        assert result.height==147
        differences=result.filter(status='different')
        assert differences.height==24
        assert set(differences['group'])=={'Contrôle','FMR1 KO'}
    else:
        assert result.filter(status='different').is_empty()
        assert result.height==(84 if factor=='corner' else 21)


def test_export_preserves_undefined_mouse_and_shared_filters(tables):
    from intellicage.prism import analysis_csv
    source=tables['mouse_summary'].with_columns(
        pl.when(pl.col('animal')==1).then(None).otherwise(pl.col('visits_duration_mean_s')).alias('visits_duration_mean_s'))
    a={'table':'mouse_summary','value':'visits_duration_mean_s'}
    selected={'groups':['control'],'phases':['phase 1'],
              'exclude_mice':[{'group':'control','lot':'lot 1','animal':2}]}
    result=analysis_csv(a,source,['control','ko'],selected)
    rows=list(csv.reader(io.StringIO(result.content)))
    assert rows[0][0]==''
    assert [m['animal'] for m in result.metadata['subjects']]==[1,3]
    assert rows[1][0]!=''


def test_shared_hour_scope_is_identical_to_the_graph(tables):
    from intellicage.prism import analysis_csv
    a={'table':'activity_elapsed','value':'visits_n','by':'hour','common_levels':True}
    source=tables['activity_elapsed'];plotted=source.filter(pl.col('hour')<2)
    table=analysis_csv(a,source,['control','ko'],plotted=plotted)
    assert table.metadata['rows']==2


def test_unmapped_reference_is_not_certified():
    result=compare_reference({},ReferenceTable('other experiment','xy',['control'],1,False,'1\n','id'))
    assert result['status'][0]=='unmapped reference'


def test_xy_reference_separates_row_titles_x_and_group_values():
    table=ReferenceTable('activity','xy',['control','ko'],2,True,
                         '2026-01-01,12:00,1,2,3,4\n2026-01-02,13:00,5,6,7,8\n','id',x_columns=1)
    assert table.group_values('control')==[['1','2'],['5','6']]
    assert table.group_values('ko')==[['3','4'],['7','8']]
