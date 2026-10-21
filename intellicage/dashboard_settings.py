from nicegui import ui

from .period_controls import PeriodControls
from .statistics_controls import statistics_controls, statistics_help



class DashboardSettings:
    def __init__(self, board):
        self.board = board
        self.statistics, self.periods, self.local = {}, {}, {}
        self.area = ui.column().classes('w-full gap-2')

    async def apply(self, changes):
        owner, project = self.board.owner, self.board.owner.project
        if not project or owner.busy: return
        def rebuild():
            project.rebuild(changes)
            return True
        with owner.workspace_tabs.client:
            if await owner.task(rebuild, status='updating analysis settings…') and owner.project is project:
                await owner.install(project)

    def common(self, project):
        self.area.clear()
        self.statistics = {}
        if not project: return
        settings = project.cfg['settings']
        with self.area:
            ui.label('statistics · all groups of graphs').classes('text-sm font-medium')
            with ui.row().classes('dashboard-filters w-full items-end gap-3 p-3 rounded-lg'):
                self.statistics = statistics_controls(settings)
                async def apply_statistics():
                    await self.apply({key: field.value for key, field in self.statistics.items()})
                ui.button('apply statistics', on_click=apply_statistics).classes('dashboard-apply').mark('dashboard-statistics-apply')
            statistics_help()

    def group(self, project, key, analyses):
        tables = {a['table'] for a in analyses}
        settings = project.cfg['settings']
        if key[0] == 'day / night':
            async def apply_periods(): await self.apply(editor.values())
            editor = PeriodControls(project, apply_periods)
            self.periods[key] = editor
        fields = {}
        if not tables.intersection({'latency', 'learning_by_day', 'gate_sessions', 'gate_hourly'}): return
        with ui.row().classes('dashboard-filters w-full items-end gap-3 p-3 rounded-lg'):
            if 'latency' in tables:
                fields['latency_window_min'] = ui.number('observation window · minutes', value=settings['latency_window_min'], min=.1).props('outlined dense stack-label').classes('w-60 dashboard-selector')
                ui.label('Empty means the full phase.').classes('muted')
            if 'learning_by_day' in tables:
                fields['min_day_hours'] = ui.number('minimum recorded hours per day', value=settings['min_day_hours'], min=.1, max=24).props('outlined dense stack-label').classes('w-64 dashboard-selector')
            if tables.intersection({'gate_sessions', 'gate_hourly'}):
                fields['gate_complete_only'] = ui.checkbox('only sessions with both in and out records', value=settings['gate_complete_only'])
            async def apply_local(): await self.apply({key: field.value for key, field in fields.items()})
            ui.button('apply settings', on_click=apply_local).classes('dashboard-apply').mark('dashboard-group-apply')
        self.local[key] = fields
