from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta

import polars as pl
import pytest

from intellicage import metrics as M
from intellicage.catalogue import default_analyses
from intellicage.pipeline import DEFAULT_SETTINGS, build_tables, render_analysis
from intellicage.plots import colors_for
from intellicage.prism import analysis_csv
from intellicage.selection import select_data
from intellicage.timebins import period_windows

WINDOWS = {'period_windows': [{'name':'day','start':'10:00','end':'16:00'},
                              {'name':'night','start':'22:00','end':'04:00'}]}


@pytest.fixture
def timed_dataset(datasets):
    ds=datasets[0]
    times=[datetime(2026,5,15,9,59,59),datetime(2026,5,15,10),datetime(2026,5,15,15,59,59),
           datetime(2026,5,15,16),datetime(2026,5,15,21,59,59),datetime(2026,5,15,22),
           datetime(2026,5,16,3,59,59),datetime(2026,5,16,4),datetime(2026,5,16,10),
           datetime(2026,5,16,22),datetime(2026,5,17,4)]
    visits=pl.concat([ds.visits.head(1).with_columns(start=pl.lit(t),end=pl.lit(t+timedelta(seconds=10)),
                      VisitDuration=pl.lit(10.)) for t in times])
    pokes=pl.concat([ds.nosepokes.head(1).with_columns(start=pl.lit(t),end=pl.lit(t+timedelta(seconds=1)),
                     NosepokeDuration=pl.lit(1.),LickNumber=pl.lit(2,pl.Int64),LickDuration=pl.lit(.5)) for t in times])
    return replace(ds,start=datetime(2026,5,15),end=datetime(2026,5,17,8),sessions=None,
                   visits=visits,nosepokes=pokes,gate_sessions=None)


def activity_analyses(tables,settings):
    return [a for a in default_analyses(tables,settings) if a['section'] in ('Hourly activity','Day / night')]


def test_default_activity_is_exactly_two_sets_of_six(tables):
    analyses=activity_analyses(tables,DEFAULT_SETTINGS)
    assert Counter(a['section'] for a in analyses)=={'Hourly activity':6,'Day / night':6}
    expected={f'{kind}_{suffix}' for kind in ['visits','nosepokes','licks'] for suffix in ['n','duration_total_s']}
    for section in ['Hourly activity','Day / night']:
        assert {a['value'] for a in analyses if a['section']==section}==expected
    assert not any(a['section'] in ('Circadian activity','Daily day / night') for a in default_analyses(tables,DEFAULT_SETTINGS))


def test_default_light_cycle_is_eight_to_twenty(datasets):
    assert DEFAULT_SETTINGS['light_on']==8 and DEFAULT_SETTINGS['light_off']==20
    assert period_windows({})==[{'name':'Light','start':480,'end':1200},
                                {'name':'Dark','start':1200,'end':480}]
    hourly=M.activity_hourly(datasets[0])
    assert hourly.filter(clock_hour=7)['dark'].all()
    assert not hourly.filter(clock_hour=8)['dark'].any()
    assert not hourly.filter(clock_hour=19)['dark'].any()
    assert hourly.filter(clock_hour=20)['dark'].all()


def test_custom_windows_do_not_redefine_the_physical_light_cycle(datasets):
    settings={**DEFAULT_SETTINGS,**WINDOWS}
    tables=build_tables(datasets,settings)
    assert tables['activity_hourly'].filter(clock_hour=21)['dark'].all()
    assert not tables['activity_hourly'].filter(clock_hour=9)['dark'].any()


def test_custom_day_night_pairs_use_start_date_and_exclude_gaps(timed_dataset):
    periods=M.activity_periods(timed_dataset,WINDOWS)
    settings={**DEFAULT_SETTINGS,**WINDOWS}
    analyses=activity_analyses({'activity_periods':periods},settings)
    analysis=next(a for a in analyses if a['value']=='visits_n')
    selected=select_data(periods,analysis,['control']).filter(animal=1).sort('day','period')
    assert selected['day'].to_list()==[1,1,2,2]
    assert selected['period'].to_list()==['day','night','day','night']
    assert selected['visits_n'].to_list()==[2,2,1,1]
    assert selected['nosepokes_n'].to_list()==[2,2,1,1]
    assert selected['licks_n'].to_list()==[4,4,2,2]
    assert selected['visits_duration_total_s'].to_list()==[20.,20.,10.,10.]
    assert selected['nosepokes_duration_total_s'].to_list()==[2.,2.,1.,1.]
    assert selected['licks_duration_total_s'].to_list()==[1.,1.,.5,.5]
    assert selected['hours'].to_list()==[6.,6.,6.,6.]
    night=selected.filter(day=1,period='night').row(0,named=True)
    assert night['window_start']==datetime(2026,5,15,22)
    assert night['window_end']==datetime(2026,5,16,4)


def test_partial_pair_is_removed_together_and_can_be_enabled(timed_dataset):
    ds=replace(timed_dataset,end=datetime(2026,5,16,23))
    table=M.activity_periods(ds,WINDOWS)
    for include_partial in [False,True]:
        settings={**DEFAULT_SETTINGS,**WINDOWS,'include_partial_periods':include_partial}
        a=activity_analyses({'activity_periods':table},settings)[0]
        selected=select_data(table,a,['control'])
        assert set(selected['day'])==({1,2} if include_partial else {1})
        assert selected.group_by('group','lot','phase','animal','day').len()['len'].to_list()==[2]*(6 if include_partial else 3)
    # a night with no recording is never filled in as zero
    table=M.activity_periods(replace(timed_dataset,end=datetime(2026,5,16,20)),WINDOWS)
    a=activity_analyses({'activity_periods':table},{**DEFAULT_SETTINGS,**WINDOWS,'include_partial_periods':True})[0]
    assert set(select_data(table,a,['control'])['day'])=={1}


def test_hourly_uses_shared_complete_hours_and_two_group_lines(datasets):
    tables=build_tables(datasets,DEFAULT_SETTINGS)
    a=next(a for a in activity_analyses(tables,DEFAULT_SETTINGS) if a['section']=='Hourly activity')
    # a shortened last hour is not compared with a complete hour
    table=tables['activity_elapsed'].with_columns(
        *[pl.when((pl.col('group')=='ko') & (pl.col('hour')==47)).then(.5).otherwise(pl.col(c)).alias(c)
          for c in ['hours','window_hours']])
    groups=['control','ko']
    result=render_analysis(a,table,groups,colors_for(groups),DEFAULT_SETTINGS,{})
    assert result['data']['hour'].max()==46
    assert result['data']['hours'].unique().to_list()==[1.]
    assert not result['figure'].layout.shapes
    assert len([t for t in result['figure'].data if t.visible!='legendonly'])==2
    assert result['figure'].layout.xaxis.title.text=='Hour from phase start'
    exported=analysis_csv(a,table,groups,plotted=result['data'])
    assert exported.metadata['rows']==47


def test_day_night_plot_has_four_series_and_same_csv_selection(timed_dataset):
    other=replace(timed_dataset,group='ko',lot='lot 2')
    settings={**DEFAULT_SETTINGS,**WINDOWS}
    tables=build_tables([timed_dataset,other],settings)
    a=next(a for a in activity_analyses(tables,settings) if a['section']=='Day / night')
    groups=['control','ko']
    result=render_analysis(a,tables[a['table']],groups,colors_for(groups),settings,{})
    assert len(result['figure'].data)==4
    assert {t.name for t in result['figure'].data}=={'control · day','control · night','ko · day','ko · night'}
    assert set(result['data']['day'])=={1,2}
    exported=analysis_csv(a,tables[a['table']],groups,plotted=result['data'])
    assert exported.metadata['rows']==4
    assert exported.metadata['row_levels']==[{'day':1,'period':'day'},{'day':1,'period':'night'},
                                             {'day':2,'period':'day'},{'day':2,'period':'night'}]


def test_recording_restart_is_reported_without_dropping_a_whole_cycle(timed_dataset):
    stop=datetime(2026,5,15,12)
    sessions=pl.DataFrame({'start':[timed_dataset.start,stop+timedelta(seconds=3)],
                           'end':[stop,timed_dataset.end]})
    ds=replace(timed_dataset,sessions=sessions)
    settings={**DEFAULT_SETTINGS,**WINDOWS}
    tables=build_tables([ds],settings)
    a=next(a for a in activity_analyses(tables,settings) if a['section']=='Day / night')
    result=render_analysis(a,tables[a['table']],['control'],colors_for(['control']),settings,{})
    assert set(result['data']['day'])=={1,2}
    day=result['data'].filter(day=1,period='day',animal=1).row(0,named=True)
    assert day['phase_complete'] and not day['complete']
    assert day['recording_gap_s']==3
    assert day['hours']==pytest.approx(6-3/3600)
    assert any('interruptions' in note for note in result['notes'])
    exported=analysis_csv(a,tables[a['table']],['control'],plotted=result['data'])
    assert exported.metadata['recording']['max_gap_s']==3


def test_fully_unrecorded_hour_is_not_drawn_as_zero(datasets):
    tables=build_tables(datasets,DEFAULT_SETTINGS)
    a=next(a for a in activity_analyses(tables,DEFAULT_SETTINGS) if a['section']=='Hourly activity')
    table=tables['activity_elapsed'].with_columns(
        pl.when((pl.col('group')=='ko') & (pl.col('hour')==5)).then(0.).otherwise(pl.col('hours')).alias('hours'))
    result=render_analysis(a,table,['control','ko'],colors_for(['control','ko']),DEFAULT_SETTINGS,{})
    assert 5 not in result['data']['hour']
