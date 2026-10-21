from nicegui import ui

from .timebins import period_windows


class PeriodControls:
    def __init__(self, project, on_apply):
        self.inputs, self.ranges, self.areas = [], {}, {}
        with ui.column().classes('w-full gap-3') as self.container:
            ui.label('day / night settings').classes('text-lg font-bold')
            ui.label(f'recorded dates: {min(ds.start.date() for ds in project.datasets)} — {max(ds.end.date() for ds in project.datasets)}').classes('muted')
            for i, window in enumerate(period_windows(project.cfg['settings'])):
                name = window['name']
                ui.label({'Light': 'day', 'Dark': 'night'}.get(name, name)).classes('font-medium').mark(f'window-{i}')
                with ui.row().classes('w-full items-end gap-3 dashboard-filters p-3 rounded-lg'):
                    start = ui.input('from', value=self.clock(window['start'])).props('type=time outlined dense stack-label').classes('w-40 dashboard-selector')
                    end = ui.input('until', value=self.clock(window['end'])).props('type=time outlined dense stack-label').classes('w-40 dashboard-selector')
                    ui.button('add date range', icon='add', on_click=lambda name=name: self.add_range(name)).props('flat dense').mark(f'add-range-{i}')
                self.inputs.append((name, start, end))
                self.ranges[name] = []
                self.areas[name] = ui.column().classes('w-full gap-2')
                for rule in window.get('ranges', []): self.add_range(name, rule)
            ui.label('Dates include both endpoints and refer to the start of the window. Nights may end the next day. Default hours apply outside date ranges.').classes('muted')
            self.partial = ui.checkbox('include incomplete first / last windows', value=project.cfg['settings']['include_partial_periods']).mark('partial-windows')
            ui.label('Include windows cut short by the start or end of the recording. For example, if day is set to 08:00–20:00 but recording starts at 12:00, enabling this option includes that incomplete day in the comparison. Day and night must still form a pair.').classes('muted')
            self.style = ui.select({'timecourse': 'cycles over time', 'paired': 'paired mice · day / night'},
                label='day / night graph', value=project.cfg['settings']['period_style']).props('outlined dense stack-label').classes('w-64 dashboard-selector')
            ui.label('Paired view connects the same mouse within one cycle. Only complete pairs are plotted; all original values remain in intermediate tables.').classes('muted')
            ui.button('apply day / night settings', on_click=on_apply).classes('dashboard-apply').mark('apply-periods')

    @staticmethod
    def clock(minutes):
        return f'{minutes // 60:02}:{minutes % 60:02}'

    def add_range(self, name, rule=None):
        rule = rule or {}
        start, end = next((s, e) for n, s, e in self.inputs if n == name)
        with self.areas[name]:
            with ui.row().classes('w-full items-end border rounded-lg p-3 gap-3') as container:
                row = {'container': container}
                for key, label in [('date_from', 'from date'), ('date_to', 'through date')]:
                    row[key] = ui.input(label, value=rule.get(key, '')).props('type=date outlined dense stack-label').classes('w-44 dashboard-selector')
                for key, label, fallback in [('start', 'from', start.value), ('end', 'until', end.value)]:
                    row[key] = ui.input(label, value=self.clock(rule[key]) if rule else fallback).props('type=time outlined dense stack-label').classes('w-40 dashboard-selector')
                ui.button('remove range', icon='close', on_click=lambda: self.remove_range(name, row)).props('flat dense')
        self.ranges[name].append(row)

    def remove_range(self, name, row):
        row['container'].delete()
        self.ranges[name].remove(row)

    def values(self):
        return {'period_windows': [{'name': n, 'start': s.value, 'end': e.value,
                'ranges': [{key: row[key].value for key in ('date_from', 'date_to', 'start', 'end')}
                           for row in self.ranges[n]]} for n, s, e in self.inputs],
                'include_partial_periods': self.partial.value, 'period_style': self.style.value}
