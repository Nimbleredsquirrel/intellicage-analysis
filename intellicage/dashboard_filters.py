from __future__ import annotations

import copy
import json

import polars as pl
from nicegui import ui


class DashboardFilters:
    def __init__(self, owner):
        self.owner, self.updating, self.signature, self.project = owner, False, None, None
        self.draft, self.mouse_records = {}, {}
        with ui.row().classes('dashboard-filters w-full items-end gap-3 p-3 rounded-lg'):
            self.groups = ui.select([], label='groups', value=[], multiple=True, with_input=True, on_change=self.changed).classes('w-52')
            self.phase = ui.select([], label='phase', on_change=self.changed).classes('w-52')
            self.mice = ui.select({}, label='mice', value=[], multiple=True, with_input=True, on_change=self.changed_mice).classes('w-60')
            for control in (self.groups, self.phase, self.mice):
                control.props('outlined dense stack-label options-dense popup-content-style="max-height:300px"').classes('dashboard-selector')
            with self.groups.add_slot('selected'): self.group_summary = ui.label().classes('truncate')
            with self.mice.add_slot('selected'): self.mouse_summary = ui.label().classes('truncate')
            for control in (self.groups, self.mice):
                with control.add_slot('before-options'):
                    with ui.row().classes('gap-1 px-2'):
                        ui.button('all', on_click=lambda control=control: control.set_value(list(control.options))).props('flat dense')
                        ui.button('none', on_click=lambda control=control: control.set_value([])).props('flat dense')
            self.apply_button = ui.button('apply', on_click=self.apply).classes('dashboard-apply').mark('dashboard-filter-apply')
            self.reset_button = ui.button('reset', on_click=self.reset).props('flat').mark('dashboard-filter-reset')
            self.status = ui.label().classes('muted self-center')
        self.controls = [self.groups, self.phase, self.mice, self.apply_button, self.reset_button]
        for control in self.controls: control.disable()

    def sync(self):
        project = self.owner.project
        signature = json.dumps(project.selection, sort_keys=True) if project else None
        if project is self.project and signature == self.signature: return
        self.project, self.signature = project, signature
        self.draft = copy.deepcopy(project.selection) if project else {}
        self.fill()

    def fill(self):
        self.updating = True
        try:
            project = self.owner.project
            self.groups.set_options(project.groups if project else [], value=self.draft.get('groups', []))
            self.phase.set_options(project.phases if project else [], value=next(iter(self.draft.get('phases', [])), None))
            self.fill_mice()
            for control in self.controls: control.set_enabled(project is not None)
        finally:
            self.updating = False
        self.summaries()

    def fill_mice(self):
        project = self.owner.project
        records = (project.tables['mouse_summary'].filter(pl.col('group').is_in(self.draft.get('groups', [])),
            pl.col('phase').is_in(self.draft.get('phases', []))).select('group', 'lot', 'animal').unique().sort('group', 'lot', 'animal').to_dicts()) if project else []
        self.mouse_records = {json.dumps(m, sort_keys=True): m for m in records}
        options = {key: f"{m['group']} / {m['lot']} / {m['animal']}" for key, m in self.mouse_records.items()}
        self.mice.set_options(options, value=[key for key, m in self.mouse_records.items() if m not in self.draft.get('exclude_mice', [])])

    def summaries(self):
        selected = self.groups.value or []
        self.group_summary.set_text(', '.join(selected) if selected else 'none selected')
        self.mouse_summary.set_text(f'{len(self.mice.value or [])} of {len(self.mouse_records)} selected')
        dirty = self.owner.project is not None and self.draft != self.owner.project.selection
        self.status.set_text('not applied' if dirty else '')

    def changed(self):
        if self.updating or not self.owner.project: return
        self.draft.update(groups=list(self.groups.value or []), phases=[self.phase.value] if self.phase.value else [])
        self.updating = True
        try: self.fill_mice()
        finally: self.updating = False
        self.summaries()

    def changed_mice(self):
        if self.updating or not self.owner.project: return
        excluded = [m for m in self.draft.get('exclude_mice', []) if m not in self.mouse_records.values()]
        excluded.extend(m for key, m in self.mouse_records.items() if key not in (self.mice.value or []))
        self.draft['exclude_mice'] = excluded
        self.summaries()

    def reset(self):
        if not self.owner.project: return
        project = self.owner.project
        self.draft = {**copy.deepcopy(project.selection), 'groups': list(project.groups), 'phases': project.phases[:1], 'exclude_mice': []}
        self.fill()

    async def apply(self):
        if self.owner.busy or not self.owner.project or self.owner.project is not self.project: return
        self.owner.project.set_selection(self.draft)
        self.owner.updating_selection = True
        try: self.owner.build_filters()
        finally: self.owner.updating_selection = False
        await self.owner.show_graph()
