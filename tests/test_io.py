import pytest

from intellicage.io import load_dataset


def test_loads_typed_export_and_phase_boundaries(export_factory):
    ds = load_dataset(export_factory(), group="control", lot="lot 1", phase="phase 1")
    assert ds.animals["Animal"].to_list() == [1, 2, 3]
    assert ds.visits.height == 12
    assert ds.nosepokes.height == 15
    assert ds.learning
    assert (ds.end - ds.start).total_seconds() == 48 * 3600
    assert ds.visits["start"].null_count() == 0
    assert ds.visits["VisitDuration"].sum() == 196.


def test_exclusion_applies_to_every_event_source(export_factory):
    ds = load_dataset(export_factory(), group="control", exclude_animals=[1])
    for frame in (ds.animals, ds.visits, ds.nosepokes, ds.gate_sessions):
        assert set(frame["Animal"]) == {2, 3}


def test_gate_is_optional_and_learning_is_detected(export_factory):
    path = export_factory(learning=False, gate=False)
    ds = load_dataset(path, group="control")
    assert ds.gate_sessions is None
    assert not ds.learning
    assert load_dataset(path, group="control", learning=True).learning


def test_non_export_has_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="no Visit.txt"):
        load_dataset(tmp_path, group="control")


@pytest.mark.parametrize('table,column,value,match', [
    ('Visit','VisitDuration',-1.,'negative values'),
    ('Visit','VisitDuration',float('nan'),'non-finite'),
    ('Visit','Corner',5,'corner ids'),
    ('Visit','Animal',999,'missing from Animal'),
    ('Visit','StartTime','invalid','missing or invalid'),
    ('Visit','EndDate','2000-01-01','negative time'),
    ('Visit','VisitID',1,'duplicate visit'),
    ('Nosepoke','VisitID',999,'missing visits'),
    ('Animal','Animal',1,'unique, valid'),
    ('Sessions','SessionEnd','2020-01-01 00:00:00','negative time'),
])
def test_invalid_events_are_rejected(export_factory, table, column, value, match):
    import polars as pl
    path=export_factory()
    file=path/f'{table}.txt'
    data=pl.read_csv(file,separator='\t').with_columns(pl.lit(value).alias(column))
    data.write_csv(file,separator='\t')
    with pytest.raises(ValueError,match=match): load_dataset(path,group='a')


def test_missing_header_has_a_clear_error(export_factory):
    import polars as pl
    path=export_factory()
    file=path/'Visit.txt'
    pl.read_csv(file,separator='\t').drop('StartTime').write_csv(file,separator='\t')
    with pytest.raises(ValueError,match='missing columns: StartTime'): load_dataset(path,group='a')


def test_missing_metric_is_rejected(export_factory):
    import polars as pl
    path=export_factory()
    file=path/'Visit.txt'
    pl.read_csv(file,separator='\t').drop('LickContactTime').write_csv(file,separator='\t')
    with pytest.raises(ValueError,match='missing columns'): load_dataset(path,group='a')


def test_zero_recording_duration_is_rejected(export_factory):
    import polars as pl
    path=export_factory()
    file=path/'Sessions.txt'
    pl.read_csv(file,separator='\t').with_columns(pl.col('SessionStart').alias('SessionEnd')).write_csv(file,separator='\t')
    with pytest.raises(ValueError,match='positive recording'): load_dataset(path,group='a')


def test_silent_mice_are_preserved_with_a_warning(export_factory):
    import polars as pl
    path=export_factory()
    file=path/'Animal.txt'
    pl.DataFrame({'Animal':[1,2,3,4]}).write_csv(file,separator='\t')
    ds=load_dataset(path,group='a')
    assert 4 in ds.animals['Animal']
    assert 'No visits recorded for animals: 4' in ds.warnings


def test_inconsistent_counts_are_reported(export_factory):
    import polars as pl
    path=export_factory()
    file=path/'Visit.txt'
    pl.read_csv(file,separator='\t').with_columns(pl.lit(0).alias('NosepokeNumber'),pl.lit(0).alias('LickNumber')).write_csv(file,separator='\t')
    ds=load_dataset(path,group='a')
    assert len(ds.warnings) == 2


@pytest.mark.parametrize('column,value,match',[('Start','bad','invalid AnimalGate'),('End','2000-01-01 00:00:00.000','negative AnimalGate')])
def test_invalid_gate_records_are_rejected(export_factory,column,value,match):
    import polars as pl
    path=export_factory()
    file=path/'AnimalGate/Session.txt'
    pl.read_csv(file,separator='\t').with_columns(pl.lit(value).alias(column)).write_csv(file,separator='\t')
    with pytest.raises(ValueError,match=match): load_dataset(path,group='a')
