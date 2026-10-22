import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timedelta

import polars as pl
import pytest
from nicegui import ui
from tests.ui_simulation import user_simulation
from polars.testing import assert_frame_equal

from intellicage import metrics as M
from intellicage.interface import Explorer
from intellicage.project import Project
from intellicage.timebins import period_bins, period_windows


def rule(first, last, start, end):
    return {'date_from': first, 'date_to': last, 'start': start, 'end': end}


@pytest.fixture
def schedule():
    return {'period_windows': [
        {'name': 'day', 'start': '08:00', 'end': '20:00', 'ranges': [
            rule('2026-10-01', '2026-10-05', '10:00', '16:00'),
            rule('2026-10-06', '2026-10-10', '09:00', '17:00')]},
        {'name': 'night', 'start': '20:00', 'end': '08:00', 'ranges': [
            rule('2026-10-01', '2026-10-05', '22:00', '04:00'),
            rule('2026-10-06', '2026-10-10', '21:00', '07:00')]}]}


@pytest.fixture
def calendar_dataset(datasets):
    return replace(datasets[0], start=datetime(2026, 9, 30), end=datetime(2026, 10, 12), sessions=None)


def test_date_ranges_are_inclusive_and_defaults_apply_outside(calendar_dataset, schedule):
    bins = period_bins(calendar_dataset, schedule)
    for day, length, night_length in [(1, 6., 6.), (5, 6., 6.), (6, 8., 10.), (10, 8., 10.), (11, 12., 12.)]:
        date_rows = bins.filter(pl.col('window_start').dt.date() == datetime(2026, 10, day).date())
        assert date_rows.sort('period')['window_hours'].to_list() == [length, night_length]
    first_night = bins.filter(pl.col('window_start') == datetime(2026, 10, 5, 22)).row(0, named=True)
    assert first_night['window_end'] == datetime(2026, 10, 6, 4)
    assert first_night['hours'] == 6
    previous = bins.filter(pl.col('window_start') == datetime(2026, 9, 30, 8)).row(0, named=True)
    assert previous['window_end'] == datetime(2026, 9, 30, 20)


def test_scheduled_windows_count_events_at_start_and_exclude_end(calendar_dataset, schedule):
    ds = calendar_dataset
    times = [datetime(2026, 10, 5, 10), datetime(2026, 10, 5, 16), datetime(2026, 10, 5, 22),
             datetime(2026, 10, 6, 3, 59), datetime(2026, 10, 6, 4), datetime(2026, 10, 6, 9),
             datetime(2026, 10, 6, 17), datetime(2026, 10, 6, 21), datetime(2026, 10, 7, 6, 59), datetime(2026, 10, 7, 7)]
    visits = pl.concat([ds.visits.head(1).with_columns(start=pl.lit(t), end=pl.lit(t + timedelta(seconds=10)),
                       VisitDuration=pl.lit(10.)) for t in times])
    pokes = pl.concat([ds.nosepokes.head(1).with_columns(start=pl.lit(t), end=pl.lit(t + timedelta(seconds=1)),
                      NosepokeDuration=pl.lit(1.), LickNumber=pl.lit(2), LickDuration=pl.lit(.5)) for t in times])
    result = M.activity_periods(replace(ds, visits=visits, nosepokes=pokes), schedule).filter(animal=1)
    chosen = result.filter(pl.col('window_start').dt.date().is_in([datetime(2026, 10, d).date() for d in (5, 6)]))
    chosen = chosen.sort('window_start')
    assert chosen['visits_n'].to_list() == [1, 2, 1, 2]
    assert chosen['nosepokes_n'].to_list() == [1, 2, 1, 2]
    assert chosen['licks_n'].to_list() == [2, 4, 2, 4]
    assert chosen['visits_duration_total_s'].to_list() == [10., 20., 10., 20.]
    assert chosen['hours'].to_list() == [6., 6., 8., 10.]
    assert chosen['nosepokes_duration_total_s'].to_list() == [1., 2., 1., 2.]
    assert chosen['licks_duration_total_s'].to_list() == [.5, 1., .5, 1.]


@pytest.mark.parametrize('rules, message', [
    ([rule('2026-10-05', '2026-10-01', '10:00', '16:00')], 'end on or after'),
    ([rule('', '2026-10-01', '10:00', '16:00')], 'YYYY-MM-DD'),
    ([rule('2026-02-30', '2026-10-01', '10:00', '16:00')], 'YYYY-MM-DD'),
    ([rule('2026-10-01', '2026-10-05', '10:00', '10:00')], 'different'),
    ([rule('2026-10-01', '2026-10-05', '10:00', '16:00'),
      rule('2026-10-05', '2026-10-10', '10:00', '16:00')], 'date ranges must not overlap'),
    ([rule('2026-10-06', '2026-10-10', '06:00', '18:00')], 'across midnight'),
    ([rule('2026-10-06', '2026-10-10', '10:00', '23:00')], 'overlap'),
])
def test_invalid_schedules_are_rejected(schedule, rules, message):
    schedule['period_windows'][0]['ranges'] = rules
    schedule['period_windows'][1]['ranges'] = []
    with pytest.raises(ValueError, match=message): period_windows(schedule)


def test_same_dates_for_day_and_night_are_allowed_and_rules_are_sorted(schedule):
    schedule['period_windows'][0]['ranges'].reverse()
    windows = period_windows(schedule)
    assert windows[0]['ranges'][0]['date_from'] == '2026-10-01'


def test_saved_project_and_batch_csv_preserve_the_schedule(config, tmp_path):
    from intellicage.__main__ import main
    cfg = copy.deepcopy(config)
    cfg['analyses'] = 'all'
    cfg['settings'] = {'period_windows': [
        {'name': 'day', 'start': '08:00', 'end': '20:00', 'ranges': [rule('2026-01-01', '2026-01-01', '08:00', '10:00')]},
        {'name': 'night', 'start': '20:00', 'end': '08:00'}]}
    project = Project(cfg)
    path = tmp_path/'study.icage'
    path.write_bytes(project.save())
    reopened = Project.open(path.read_bytes(), tmp_path/'opened')
    assert reopened.cfg['settings']['period_windows'] == project.cfg['settings']['period_windows']
    assert_frame_equal(reopened.tables['activity_periods'], project.tables['activity_periods'])
    analysis = next(a for a in project.analyses if a['section'] == 'Day / night')
    expected = project.result(analysis['name'])['data']
    output = main([str(path), '--only', analysis['name']])
    actual = pl.read_csv(output/'chart_data'/f'{analysis["name"]}.csv', try_parse_dates=True)
    columns = ['group', 'lot', 'phase', 'animal', 'day', 'period', 'visits_n', 'hours', 'window_hours', 'window_start', 'window_end']
    assert_frame_equal(actual.select(columns), expected.select(columns), check_dtypes=False)


@pytest.mark.parametrize('rules', [None, {}, ['not a rule']])
def test_malformed_range_lists_are_reported(schedule, rules):
    schedule['period_windows'][0]['ranges'] = rules
    with pytest.raises(ValueError, match='list of window rules'): period_windows(schedule)


def test_calendar_edges_are_handled_without_overflow(schedule):
    schedule['period_windows'][0]['ranges'] = [rule('0001-01-01', '0001-01-02', '10:00', '16:00')]
    period_windows(schedule)
    schedule['period_windows'][0]['ranges'] = [rule('9999-12-31', '9999-12-31', '10:00', '16:00')]
    with pytest.raises(ValueError, match='supported calendar'): period_windows(schedule)


@pytest.mark.asyncio
async def test_ui_date_rules_apply_remove_and_restore(config, tmp_path):
    config['analyses'] = 'all'
    pages = []
    async def root():
        page = Explorer(None, local=True, native=True)
        pages.append(page)
        await page.install(Project(config))
    async with user_simulation(root) as user:
        await user.open('/')
        page = pages[0]
        assert any(isinstance(e, ui.label) and e.text == 'day' for e in page.calculations.descendants())
        assert any(isinstance(e, ui.label) and e.text == 'night' for e in page.calculations.descendants())
        assert not any(isinstance(e, ui.label) and e.text == 'calculation settings' for e in page.filters.descendants())
        assert not page.window_settings.visible
        assert page.drawer in list(page.sources.ancestors())
        assert page.drawer not in list(page.calculations.ancestors())
        assert page.calculations.id < page.section.id
        assert not any(isinstance(e, ui.expansion) and e.text in ('day', 'night', 'calculation settings')
                       for e in page.calculations.descendants())
        before = page.project.tables['activity_periods'].clone()
        with user.client: page.analysis_view.set_value('day / night')
        await asyncio.sleep(.1)
        assert page.window_settings.visible
        graph = page.name
        for index, start, end in [(0, '10:00', '16:00'), (1, '22:00', '04:00')]:
            user.find(marker=f'add-range-{index}').click()
            name = page.window_inputs[index][0]
            row = page.window_ranges[name][0]
            with user.client:
                row['date_from'].set_value('2026-01-01')
                row['date_to'].set_value('2026-01-01')
                row['start'].set_value(start)
                row['end'].set_value(end)
        with user.client: await page.recalculate()
        assert not page.project.tables['activity_periods'].equals(before)
        assert page.name == graph
        restored = Project.open(page.project.save(), tmp_path/'restored')
        with user.client: await page.install(restored)
        name = page.window_inputs[0][0]
        assert page.window_ranges[name][0]['start'].value == '10:00'
        assert page.window_ranges[name][0]['date_to'].value == '2026-01-01'
        # invalid edits must leave the last successful calculation intact
        old_tables = page.project.tables
        with user.client:
            page.window_ranges[name][0]['date_to'].set_value('2025-12-31')
            await page.recalculate()
        assert page.project.tables is old_tables
        assert user.notify.contains('end on or after')
        with user.client:
            for name, _, _ in page.window_inputs:
                page.remove_window_range(name, page.window_ranges[name][0])
            await page.recalculate()
        assert_frame_equal(page.project.tables['activity_periods'], before)
