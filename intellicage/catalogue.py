from __future__ import annotations

import re

from .timebins import period_windows


def analysis_name(a: dict) -> str:
    raw = a.get('name') or '_'.join(str(a[k]) for k in ('table', 'value', 'by', 'plot') if a.get(k))
    name = re.sub(r'[^A-Za-z0-9_-]+', '_', raw).strip('_').lower()
    if not name:
        raise ValueError('Analysis names need at least one ASCII letter or digit')
    return name


def default_analyses(tables: dict, settings: dict) -> list[dict]:
    result = []

    def add(table, value, section, title, ylabel, *, by=None, plot=None, **options):
        if table not in tables or (value and value not in tables[table].columns):
            return
        a = {'table': table, 'section': section, 'title': title, 'ylabel': ylabel, **options}
        if value:
            a['value'] = value
        if by:
            a['by'] = by
        if plot:
            a['plot'] = plot
        a['name'] = analysis_name(a)
        result.append(a)

    for kind, label in [('visits', 'Visits'), ('nosepokes', 'Nosepokes'), ('licks', 'Licks')]:
        for suffix, title, unit in [('n', f'{label}: total number', 'Number'),
                                    ('duration_total_s', f'{label}: total duration', 'Duration (s)'),
                                    ('duration_mean_s', f'{label}: mean duration', 'Mean duration (s)')]:
            add('mouse_summary', f'{kind}_{suffix}', label, title, unit)
    for value, section, title, unit in [
        ('visits_with_nosepoke_n', 'Visits', 'Visits with nosepokes', 'Visits (n)'),
        ('visits_without_nosepoke_n', 'Visits', 'Visits without nosepokes', 'Visits (n)'),
        ('targeted_exploration_pct', 'Visits', 'Targeted exploration', 'Visits with nosepokes (%)'),
        ('nosepokes_with_licks_n', 'Nosepokes', 'Nosepokes with licks', 'Nosepokes (n)'),
        ('nosepokes_without_licks_n', 'Nosepokes', 'Nosepokes without licks', 'Nosepokes (n)'),
        ('licks_duration_per_visit_s', 'Licks', 'Lick duration per visit · Prism reference', 'Lick duration / visit (s)'),
        ('nosepokes_correct_n', 'Learning', 'Correct nosepokes: total number', 'Nosepokes (n)'),
        ('nosepokes_incorrect_n', 'Learning', 'Incorrect nosepokes: total number', 'Nosepokes (n)'),
        ('nosepokes_correct_pct', 'Learning', 'Correct nosepokes', 'Correct nosepokes (%)'),
        ('nosepokes_incorrect_pct', 'Learning', 'Incorrect nosepokes', 'Incorrect nosepokes (%)'),
        ('thirst_index', 'Learning', 'Thirst index', 'Licks / correct nosepoke'),
    ]:
        add('mouse_summary', value, section, title, unit)
    for value, title, unit in [('visits_n', 'Visits per corner', 'Visits (n)'),
                                ('time_s', 'Time spent in each corner', 'Time (s)'),
                                ('time_pct', 'Share of time in corners', 'Time in corners (%)'),
                                ('visits_pct', 'Share of visits per corner', 'Visits (%)'),
                                ('visit_duration_mean_s', 'Mean visit duration per corner', 'Mean visit duration (s)')]:
        add('corner_summary', value, 'Corners', title, unit, by='corner')
    for value, title, unit in [('correct_n', 'Correct nosepokes by day', 'Nosepokes (n)'),
                                ('incorrect_n', 'Incorrect nosepokes by day', 'Nosepokes (n)'),
                                ('correct_pct', 'Correct nosepokes by day', 'Correct nosepokes (%)'),
                                ('incorrect_pct', 'Associative learning', 'Incorrect nosepokes (%)')]:
        add('learning_by_day', value, 'Learning', title, unit, by='day')
    add('latency', 'latency_first_visit_s', 'Latency', 'Latency to first visit from phase start', 'Latency (s)', plot='latency')
    for corner in range(1, 5):
        add('latency', f'latency_corner_{corner}_s', 'Latency', f'First visit to corner {corner}', 'Latency (s)', plot='latency')
    add('latency', 'first_visit_duration_s', 'First visit duration', 'Duration of first visit to any corner', 'Duration (s)')
    for corner in range(1, 5):
        add('latency', f'corner_{corner}_duration_s', 'First visit duration', f'Duration of first visit to corner {corner}', 'Duration (s)')

    periods = [window['name'] for window in period_windows(settings)]
    for kind, label in [('visits', 'Visits'), ('nosepokes', 'Nosepokes'), ('licks', 'Licks')]:
        for metric, noun, unit in [('n', 'number', 'Number'),
                                   ('duration_total_s', 'total duration', 'Duration (s)')]:
            add('activity_elapsed', f'{kind}_{metric}', 'Hourly activity',
                f'{label}: {noun} by hour from phase start', unit, by='hour',
                xlabel='Hour from phase start', common_levels=True, shade_dark=False,
                observed_only=True, filter={'window_hours': 1.0})
            options = {} if settings.get('include_partial_periods', False) else {'filter': {'phase_complete': True}}
            add('activity_periods', f'{kind}_{metric}', 'Day / night',
                f'{label}: {noun} in each day / night cycle', unit, by='day',
                plot='period_timecourse', paired_periods=periods, **options)

    gate_filter = {'paired': True} if settings.get('gate_complete_only') else {}
    add('gate_sessions', None, 'AnimalGate', 'AnimalGate sessions', 'Mouse', plot='gate_raster', filter=gate_filter)
    for value, title, unit in [('gate_sessions_n', 'AnimalGate: number of sessions', 'Sessions (n)'),
                                ('gate_first_session_h', 'AnimalGate: first session', 'Time from phase start (h)'),
                                ('gate_duration_mean_s', 'AnimalGate: mean session duration', 'Mean session duration (s)'),
                                ('gate_duration_total_s', 'AnimalGate: total session duration', 'Session duration (s)'),
                                ('visits_per_gate_session', 'Visits per AnimalGate session', 'Visits / session'),
                                ('weight_mean_g', 'AnimalGate: mean body weight', 'Weight (g)'),
                                ('gate_unpaired_sessions_n', 'AnimalGate: single-direction records', 'Records (n)')]:
        add('gate_summary', value, 'AnimalGate', title, unit)
    for value, title, unit in [('gate_sessions_n', 'AnimalGate: sessions started per hour', 'Sessions (n)'),
                                ('gate_duration_total_s', 'AnimalGate: session duration by starting hour', 'Session duration (s)'),
                                ('gate_duration_mean_s', 'AnimalGate: mean duration by starting hour', 'Mean session duration (s)'),
                                ('visits_per_gate_session', 'Visits per gate session by hour', 'Visits / session')]:
        add('gate_hourly', value, 'AnimalGate hourly', title, unit, by='hour', common_levels=True)
    return result


def analysis_section(a):
    if a.get('table') in ('activity_periods', 'activity_light_dark') or a.get('plot') == 'period_timecourse':
        view = 'day / night'
    elif a.get('table') in ('activity_hourly', 'activity_elapsed', 'activity_circadian', 'gate_hourly'):
        view = 'hourly activity'
    else:
        view = 'overview'
    return view, a.get('section', 'Analysis')
