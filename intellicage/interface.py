from __future__ import annotations

import argparse
import asyncio
import copy
import io
import zipfile
import json
import tempfile
from time import perf_counter
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from nicegui import app, native, run, ui
import yaml
import polars as pl

from .catalogue import analysis_name
from .board import GraphBoard
from .table_view import TablePreview
from .metadata import METADATA_FILE, group_hint, validate_labels
from .plots import PLOT_CONFIG, colors_for
from .project import MAX_FILES, MAX_UPLOAD, Project, discover, safe_name, unpack
from .prism import analysis_csv
from .selection import select_data
from .timebins import period_windows

ROOT = Path(__file__).resolve().parents[1]
IMPORTS = {}
APP_NAME = 'intellicage analysis'


@app.post('/imports/{token}')
async def import_folder(token: str, request: Request):
    page = IMPORTS.get(token)
    if page is None:
        raise HTTPException(404, 'Import session expired')
    destination = page.workspace / uuid4().hex
    total, count, seen = 0, 0, set()
    try:
        async with request.form(max_files=MAX_FILES, max_fields=10) as form:
            for _, file in form.multi_items():
                if not hasattr(file, 'filename'):
                    continue
                name = safe_name(file.filename)
                if name in seen:
                    raise ValueError('Duplicate file paths')
                seen.add(name)
                if not name.lower().endswith('.txt') and Path(name).name != METADATA_FILE:
                    continue
                count += 1
                if count > MAX_FILES:
                    raise ValueError('Too many files')
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('wb') as target:
                    while chunk := await file.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_UPLOAD:
                            raise ValueError('Folder exceeds 1 GB')
                        target.write(chunk)
        specs = await run.io_bound(discover, destination)
        # browser upload is followed by the load button in its own client
        page.pending_specs = specs
        return {'datasets': len(specs)}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


def source_paths(dataset):
    return {Path(path).expanduser().resolve() for path in [dataset['path'], dataset.get('source_path')] if path}


class Explorer:
    def __init__(self, config: Path | None, local: bool, native: bool):
        self.config, self.local, self.native = config, local, native
        self.project = None
        self.pending_specs = None
        self.name = None
        self.busy = False
        self.dataset_labels = {}
        self.import_dialog = None
        self.defaults = {}
        self.updating_selection = False
        self.mice_open = False
        self.metadata_dialog = None
        self.updating_choices = False
        self.render_version = 0
        self.render_lock = asyncio.Lock()
        self.temporary = tempfile.TemporaryDirectory(prefix='intellicage-')
        self.workspace = Path(self.temporary.name)
        self.token = uuid4().hex
        IMPORTS[self.token] = self
        ui.context.client.on_delete(self.cleanup)
        self.build()
        if config and config.is_file():
            ui.timer(.1, self.initial_load, once=True)

    def cleanup(self):
        IMPORTS.pop(self.token, None)
        self.temporary.cleanup()

    async def task(self, function, *args, status='working…'):
        if self.busy:
            return None
        self.busy = True
        started = perf_counter()
        self.status.set_text(status)
        for control in self.task_controls + self.folder_remove_buttons + self.folder_edit_buttons:
            control.disable()
        self.progress.set_visibility(True)
        try:
            result = await run.io_bound(function, *args)
            self.status.set_text(f'done · {perf_counter() - started:.1f} s')
            return result
        except Exception as exc:
            self.status.set_text('could not finish · see the error message')
            ui.notify(str(exc), type='negative', close_button=True, timeout=10000)
            return None
        finally:
            self.busy = False
            for control in self.task_controls + self.folder_remove_buttons + self.folder_edit_buttons:
                control.enable()
            self.progress.set_visibility(False)

    async def initial_load(self):
        def load():
            cfg = yaml.safe_load(self.config.read_text())
            self.defaults = {key: value for key, value in cfg.items() if key in ('settings', 'analyses', 'output_dir')}
            if not cfg.get('datasets'):
                return None
            for dataset in cfg['datasets']:
                path = Path(dataset['path']).expanduser()
                dataset['path'] = str(path if path.is_absolute() else self.config.parent / path)
            return Project(cfg)
        project = await self.task(load, status='reading exports and calculating tables…')
        if project:
            await self.install(project)

    def build(self):
        ui.colors(primary='#2a78d6', secondary='#eb6834', accent='#2a78d6')
        ui.add_css('''body{background:#f6f7f9;color:#222222}.q-card{border:1px solid #e1e4e8;box-shadow:none;border-radius:14px}
.nicegui-content{max-width:1500px;margin:auto;padding:28px;width:100%}.q-drawer{background:#ffffff}
h1,h2{letter-spacing:-.025em}.plotly-graph-div{border-radius:12px}.muted{color:#667085;font-size:13px}
.chart-window{resize:both;overflow:hidden;flex:0 0 auto;max-width:100%;min-width:min(320px,100%);min-height:300px}
.chart-drag-handle{touch-action:none}.chart-window .js-plotly-plot{min-height:0!important}
.nicegui-tab-panel{padding:0;overflow:visible}.q-expansion-item{flex-shrink:0}''')
        with ui.header().classes('bg-white text-primary border-b items-center px-6'):
            ui.button(icon='menu', on_click=lambda: self.drawer.toggle()).props('flat round')
            ui.label(APP_NAME).classes('text-xl font-bold')
            ui.space()
            self.new_button = ui.button('new project', icon='note_add', on_click=self.new_project).props('flat').mark('new-project')
            self.open_button = ui.button('open project', icon='folder_open', on_click=self.native_project).props('flat').mark('open-project') if self.native else None
            self.save_button = ui.button('save project', icon='save', on_click=self.save_project).props('flat').mark('save-project')
            self.export_button = ui.button('export all', icon='download', on_click=self.export_all).mark('export-all')
        self.status = ui.label('ready').classes('muted text-sm').mark('operation-status')
        self.progress = ui.linear_progress().props('indeterminate').classes('w-full')
        self.progress.set_visibility(False)
        with ui.left_drawer(value=True).classes('p-0').props('width=300') as self.drawer:
            with ui.scroll_area().classes('w-full h-full').style('height:100%').mark('sidebar-scroll'):
                with ui.column().classes('w-full p-5 gap-3 shrink-0'):
                    self.folder_button = None
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('your data').classes('text-xl font-bold')
                        if self.native:
                            self.folder_button = ui.button(icon='add', on_click=self.native_folder).props('outline round').mark('add-folders').tooltip('add folders')
                    if not self.native:
                        # relative paths preserve the animal gate subfolder
                        ui.html(f'''<label style="display:block;padding:12px;background:#edf4fc;border-radius:8px;cursor:pointer">
            choose export folder<input style="display:none" type="file" webkitdirectory multiple onchange="
            const status=document.getElementById('folder-status-{self.token}');
            status.textContent='uploading…';const form=new FormData();
            for(const f of this.files){{if(f.name.toLowerCase().endsWith('.txt')||f.name==='intellicage.json')form.append('files',f,f.webkitRelativePath);}}
            fetch('/imports/{self.token}',{{method:'POST',body:form}}).then(async r=>{{const v=await r.json();
            status.textContent=r.ok?v.datasets+' exports ready — click load folder':v.detail;}}).catch(e=>status.textContent=e.message);"></label><small id="folder-status-{self.token}"></small>''', sanitize=False)
                        ui.button('load folder', on_click=self.load_pending).props('flat').classes('w-full').mark('load-folder')
                        ui.upload(label='ZIP / saved project', auto_upload=True, max_file_size=MAX_UPLOAD,
                                  on_upload=self.upload, on_rejected=lambda: ui.notify('File exceeds the upload limit', type='negative')).props('accept=.zip,.icage').classes('w-full')
                    self.folder_list = ui.column().classes('w-full gap-2').mark('selected-folders')
                    self.show_folders()
                    ui.separator().classes('my-4')
                    self.filters = ui.column().classes('w-full gap-3')
                    ui.separator().classes('my-2')
                    self.sources = ui.column().classes('w-full gap-2 break-words').mark('exports-checks')
        self.heading = ui.label(APP_NAME).classes('text-3xl font-bold')
        self.summary = ui.label('Load your exports to begin.').classes('muted')
        with ui.tabs(value='graphs', on_change=self.change_workspace).classes('w-full').props('align=left no-caps').mark('workspace-tabs') as self.workspace_tabs:
            ui.tab('graphs', label='graphs', icon='bar_chart')
            self.page_tab = ui.tab('page', label='dashboard', icon='dashboard')
            ui.tab('tables', label='tables', icon='table_chart')
        with ui.tab_panels(self.workspace_tabs, value='graphs', animated=False, keep_alive=False).classes('w-full bg-transparent'):
            with ui.tab_panel('graphs').classes('w-full gap-4'):
                self.analysis_view = ui.toggle(['overview', 'hourly activity', 'day / night'], value='overview', on_change=self.choose_view).props('no-caps').mark('analysis-view')
                self.calculations = ui.column().classes('w-full gap-3').mark('calculation-settings')
                with ui.row().classes('w-full items-end gap-4'):
                    self.section = ui.select([], label='measurements', on_change=self.choose_section).classes('w-64').mark('section')
                    self.graph_select = ui.select({}, label='graph', with_input=True, on_change=self.choose_graph).classes('grow').mark('graph')
                    self.add_graph_button = ui.button('add to dashboard', icon='add', on_click=lambda: self.board.add(self.name)).mark('add-to-page').disable()
                self.graph_area = ui.column().classes('w-full')
                self.editor_area = ui.column().classes('w-full')
                self.details_area = ui.column().classes('w-full')
            with ui.tab_panel('page').classes('w-full'):
                self.board = GraphBoard(self)
            with ui.tab_panel('tables').classes('w-full'):
                ui.label('tables').classes('text-lg font-bold')
                ui.label('Full intermediate tables for the current groups, phase and mice. Downloads include every selected row and column.').classes('muted')
                self.table_select = ui.select({}, label='table', on_change=self.show_table).classes('w-96 max-w-full').mark('table-choice')
                self.table_content = ui.column().classes('w-full min-w-0').mark('table-preview')
        self.task_controls = [self.new_button, self.save_button, self.export_button, self.section, self.graph_select, self.analysis_view]
        self.task_controls.extend(button for button in [self.folder_button, self.open_button, self.board.apply_button] if button is not None)

    def show_folders(self):
        self.folder_list.clear()
        self.folder_remove_buttons = []
        self.folder_edit_buttons = []
        with self.folder_list:
            if not self.project:
                ui.label('no folders added').classes('muted text-sm')
                return
            for i, ds in enumerate(self.project.datasets):
                with ui.column().classes('w-full gap-1 border rounded-lg p-2'):
                    with ui.row().classes('w-full items-start no-wrap'):
                        ui.label(ds.path.name).classes('font-medium break-words grow min-w-0')
                        button = ui.button(icon='close', on_click=lambda path=ds.path: self.remove_folder(path)).props('flat round dense aria-label="remove folder"').mark(f'remove-folder-{i}').tooltip('remove folder from project')
                        self.folder_remove_buttons.append(button)
                    ui.label(str(ds.path.expanduser().resolve())).classes('muted text-xs break-all w-full')
                    ui.label(' · '.join(filter(None, (ds.group, ds.lot, ds.phase)))).classes('muted text-sm')
                    edit = ui.button('edit labels', icon='edit', on_click=lambda i=i: self.edit_labels(i)).props('flat dense').mark(f'edit-labels-{i}')
                    self.folder_edit_buttons.append(edit)

    async def install(self, project, *, reset_labels=False):
        previous_section, previous_graph = self.section.value, self.name
        self.project = project
        if reset_labels: self.dataset_labels.clear()
        self.dataset_labels.update({path: copy.deepcopy(ds) for ds in project.cfg['datasets']
                                    for path in source_paths(ds)})
        self.show_folders()
        self.heading.set_text(project.cfg.get('run_name', APP_NAME))
        self.summary.set_text(f'{sum(d.animals.height for d in project.datasets)} mice · {len(project.datasets)} exports · {len(project.analyses)} graphs')
        self.sources.clear()
        with self.sources:
            with ui.expansion('exports & data checks').classes('w-full'):
                for ds in project.datasets:
                    ui.label(f'{ds.group} / {ds.lot} / {ds.phase} · {ds.animals.height} mice · {ds.visits.height:,} visits')
                    for warning in ds.warnings:
                        ui.label(warning).classes('text-amber-900 text-sm')
                gate = project.tables.get('gate_sessions')
                if gate is not None:
                    ui.label(f'AnimalGate: {gate.filter(~pl.col("paired")).height} single-direction records. Keep all records or use paired records only in settings.').classes('muted')
        self.build_filters()
        self.build_calculations()
        self.board.prepare()
        self.updating_choices = True
        try:
            self.section.set_options(self.sections(), value=previous_section if previous_section in self.sections() else next(iter(self.sections()), None))
            self.graph_choices(previous_graph)
            options = {name: name.replace('_', ' ') for name in project.tables}
            self.table_select.set_options(options, value=self.table_select.value if self.table_select.value in options else next(iter(options), None))
        finally:
            self.updating_choices = False
        self.update_windows_visibility()
        await self.show_graph()

    def build_filters(self):
        p = self.project
        self.filters.clear()
        with self.filters:
            ui.label('selection').classes('text-lg font-bold')
            ui.label('groups').classes('font-medium')
            self.group_checkboxes = {}
            for i, group in enumerate(p.groups):
                box = ui.checkbox(group, value=group in p.selection['groups']).mark(f'group-{i}')
                self.group_checkboxes[group] = box
                box.on_value_change(self.choose_selection)
            ui.label('phase').classes('font-medium')
            self.phase_select = ui.radio(p.phases, value=next(iter(p.selection['phases']), None)).mark('phase')
            self.phase_select.on_value_change(self.choose_selection)
            with ui.expansion('mice', value=self.mice_open, on_value_change=lambda e: setattr(self, 'mice_open', e.value)).classes('w-full mice-selector').props('dense header-class=font-medium').mark('mice-selection') as self.mice_expansion:
                with ui.row().classes('gap-1'):
                    ui.button('select all', on_click=lambda: self.set_mice(True)).props('flat dense').mark('select-all-mice')
                    ui.button('clear all', on_click=lambda: self.set_mice(False)).props('flat dense').mark('clear-all-mice')
                self.mice_area = ui.column().classes('w-full mice-list').style('display:block;max-height:40vh;overflow-y:auto;overscroll-behavior:contain').mark('mice')
                self.show_mice()

    def build_calculations(self):
        p = self.project
        self.calculations.clear()
        with self.calculations:
            self.window_inputs, self.window_ranges, self.range_areas = [], {}, {}
            with ui.column().classes('w-full gap-3') as self.window_settings:
                ui.label('calculation settings').classes('text-lg font-bold')
                ui.label(f'recorded dates: {min(ds.start.date() for ds in p.datasets)} — {max(ds.end.date() for ds in p.datasets)}').classes('muted')
                windows = period_windows(p.cfg['settings'])
                for i, w in enumerate(windows):
                    name = w['name']
                    label = {'Light': 'day', 'Dark': 'night'}.get(name, name)
                    ui.label(label).classes('font-medium').mark(f'window-{i}')
                    with ui.column().classes('w-full gap-2'):
                        ui.label('default hours').classes('muted')
                        with ui.row().classes('items-end gap-4'):
                            start = ui.input('from', value=self.clock_text(w['start'])).props('type=time').classes('w-40 max-w-full')
                            end = ui.input('until', value=self.clock_text(w['end'])).props('type=time').classes('w-40 max-w-full')
                        self.window_inputs.append((name, start, end))
                        self.window_ranges[name] = []
                        self.range_areas[name] = ui.column().classes('w-full gap-2')
                        for rule in w.get('ranges', []): self.add_window_range(name, rule)
                        ui.button('add date range', icon='add', on_click=lambda name=name: self.add_window_range(name)).props('flat dense').mark(f'add-range-{i}')
                ui.label('Dates include both endpoints and refer to the start of the window. Nights may end the next day. Default hours apply outside date ranges.').classes('muted')
                self.partial = ui.checkbox('include incomplete first / last windows', value=p.cfg['settings']['include_partial_periods']).mark('partial-windows')
                ui.label('Include windows cut short by the start or end of the recording. For example, if day is set to 08:00–20:00 but recording starts at 12:00, enabling this option includes that incomplete day in the comparison. Day and night must still form a pair.').classes('muted')
                ui.button('apply day / night settings', on_click=self.recalculate).mark('apply-periods')
            with ui.expansion('more analysis settings').classes('w-full'):
                with ui.expansion('first visits').classes('w-full'):
                    self.latency = ui.number('observation window · minutes', value=p.cfg['settings']['latency_window_min'], min=.1).mark('first-visit-window')
                    ui.label('Leave empty for the full phase. Applies to latency and first visit duration.').classes('muted')
                with ui.expansion('learning').classes('w-full'):
                    self.min_day = ui.number('minimum recorded hours per day', value=p.cfg['settings']['min_day_hours'], min=.1, max=24)
                with ui.expansion('animal gate').classes('w-full') as gate_settings:
                    self.paired = ui.checkbox('only sessions with both in and out records', value=p.cfg['settings']['gate_complete_only'])
                    ui.label('This option applies to AnimalGate only. Keep only sessions with records for both directions: In and Out. Records are matched by mouse and session start time. Sessions missing either direction are excluded.').classes('muted')
                gate_settings.set_visibility('gate_sessions' in p.tables)
                with ui.expansion('statistics and appearance').classes('w-full'):
                    self.error = ui.select(['sem', 'sd'], label='error bars', value=p.cfg['settings']['error'])
                    self.bar_style = ui.select(['bar', 'scatter'], label='summary style', value=p.cfg['settings']['bar_style'])
                    self.test = ui.select(['welch', 'student', 'mann_whitney', 'auto'], label='group test', value=p.cfg['settings']['test'])
                    self.repeated = ui.select(['mixed_anova', 'lmm'], label='repeated measures', value=p.cfg['settings']['repeated_test'])
                    self.adjust = ui.select(['sidak', 'holm', 'bonferroni', 'fdr_bh'], label='multiple comparisons', value=p.cfg['settings']['padjust'])
                ui.button('apply settings', on_click=self.recalculate).classes('w-full').mark('recalculate')

    @staticmethod
    def clock_text(minutes):
        return f'{minutes // 60:02}:{minutes % 60:02}'

    def add_window_range(self, name, rule=None):
        rule = rule or {}
        with self.range_areas[name]:
            with ui.row().classes('w-full items-end border rounded-lg p-3 gap-4') as container:
                row = {'container': container}
                row['date_from'] = ui.input('from date', value=rule.get('date_from', '')).props('type=date').classes('w-44 max-w-full')
                row['date_to'] = ui.input('through date', value=rule.get('date_to', '')).props('type=date').classes('w-44 max-w-full')
                start, end = next((s, e) for n, s, e in self.window_inputs if n == name)
                row['start'] = ui.input('from', value=self.clock_text(rule['start']) if rule else start.value).props('type=time').classes('w-40 max-w-full')
                row['end'] = ui.input('until', value=self.clock_text(rule['end']) if rule else end.value).props('type=time').classes('w-40 max-w-full')
                ui.button('remove range', icon='close', on_click=lambda: self.remove_window_range(name, row)).props('flat dense')
        self.window_ranges[name].append(row)

    def remove_window_range(self, name, row):
        self.window_ranges[name].remove(row)
        row['container'].delete()

    def show_mice(self):
        p = self.project
        self.mice_area.clear()
        self.mouse_checkboxes = {}
        mice = (p.tables['mouse_summary'].filter(pl.col('group').is_in(p.selection['groups']),
                    pl.col('phase').is_in(p.selection['phases']))
                .select('group', 'lot', 'animal').unique().sort('group', 'lot', 'animal').to_dicts())
        with self.mice_area:
            previous = None
            for mouse in mice:
                key = (mouse['group'], mouse['lot'], mouse['animal'])
                if key[:2] != previous:
                    ui.label(' · '.join(filter(None, key[:2]))).classes('muted font-medium mt-2')
                    previous = key[:2]
                box = ui.checkbox(str(mouse['animal']), value=mouse not in p.selection.get('exclude_mice', [])).classes('w-full shrink-0').mark(f'mouse-{len(self.mouse_checkboxes)}')
                self.mouse_checkboxes[key] = box
                box.on_value_change(lambda e, key=key: self.choose_mouse(key, e.value))
            if not mice:
                ui.label('No mice in the selected groups and phase.').classes('muted')
        self.mouse_caption()

    def mouse_caption(self):
        self.mice_expansion.props['caption'] = f'{sum(bool(box.value) for box in self.mouse_checkboxes.values())} of {len(self.mouse_checkboxes)} selected'

    def choose_selection(self):
        if self.updating_selection: return
        selection = {**self.project.selection,
                     'groups': [group for group, box in self.group_checkboxes.items() if box.value],
                     'phases': [self.phase_select.value] if self.phase_select.value else []}
        self.project.set_selection(selection)
        self.show_mice()
        self.render_version += 1
        return self.show_graph()

    def choose_mouse(self, key, included):
        if self.updating_selection: return
        mouse = dict(zip(('group', 'lot', 'animal'), key))
        excluded = [m for m in self.project.selection.get('exclude_mice', []) if m != mouse]
        if not included: excluded.append(mouse)
        self.project.set_selection({**self.project.selection, 'exclude_mice': excluded})
        self.mouse_caption()
        self.render_version += 1
        return self.show_graph()

    def set_mice(self, included):
        self.updating_selection = True
        try:
            for box in self.mouse_checkboxes.values(): box.set_value(included)
        finally:
            self.updating_selection = False
        visible = [dict(zip(('group', 'lot', 'animal'), key)) for key in self.mouse_checkboxes]
        excluded = [m for m in self.project.selection.get('exclude_mice', []) if m not in visible]
        if not included: excluded.extend(visible)
        self.project.set_selection({**self.project.selection, 'exclude_mice': excluded})
        self.mouse_caption()
        self.render_version += 1
        return self.show_graph()

    async def recalculate(self):
        settings = {'period_windows': [{'name': n, 'start': s.value, 'end': e.value,
                     'ranges': [{key: row[key].value for key in ('date_from', 'date_to', 'start', 'end')}
                                for row in self.window_ranges[n]]} for n, s, e in self.window_inputs],
                    'include_partial_periods': self.partial.value, 'gate_complete_only': self.paired.value,
                    'latency_window_min': self.latency.value, 'min_day_hours': self.min_day.value,
                    'error': self.error.value, 'bar_style': self.bar_style.value, 'test': self.test.value, 'repeated_test': self.repeated.value, 'padjust': self.adjust.value}
        def rebuild():
            self.project.rebuild(settings)
            return True
        if await self.task(rebuild):
            await self.show_graph()

    def edit_labels(self, index):
        if self.busy: return
        if self.metadata_dialog: self.metadata_dialog.close()
        self.edit_index = index
        ds = self.project.cfg['datasets'][index]
        with ui.dialog().props('persistent') as self.metadata_dialog, ui.card().classes('w-96 max-w-full'):
            ui.label(Path(ds['path']).name).classes('font-bold')
            self.label_inputs = [ui.input(key, value=ds.get(key, '')).classes('w-full') for key in ('group', 'lot', 'phase')]
            with ui.row():
                ui.button('cancel', on_click=self.metadata_dialog.close).props('flat')
                ui.button('save labels', on_click=self.relabel).mark('save-labels')
        self.metadata_dialog.open()

    async def relabel(self):
        if self.busy: return
        cfg = self.project.configuration()
        ds = cfg['datasets'][self.edit_index]
        old = dict(ds)
        ds.update(zip(('group', 'lot', 'phase'), (field.value.strip() for field in self.label_inputs)))
        try:
            validate_labels(ds)
        except ValueError as exc:
            ui.notify(str(exc), type='warning')
            return
        present = list(dict.fromkeys(d['group'] for d in cfg['datasets']))
        selected = set(cfg['selection']['groups'])
        if old['group'] in selected: selected.add(ds['group'])
        cfg['groups'] = list(dict.fromkeys([ds['group'] if g == old['group'] else g for g in self.project.groups] + present))
        cfg['groups'] = [g for g in cfg['groups'] if g in present]
        cfg['selection']['groups'] = [g for g in cfg['groups'] if g in selected]
        phases = {d['phase'] for d in cfg['datasets']}
        cfg['selection']['phases'] = [phase if phase in phases else ds['phase'] for phase in cfg['selection']['phases']]
        excluded = cfg['selection'].get('exclude_mice', [])
        animal_ids = self.project.datasets[self.edit_index].animals['Animal'].to_list()
        renamed = [{**m, 'group': ds['group'], 'lot': ds['lot']} for m in excluded
                   if (m['group'], m['lot']) == (old['group'], old.get('lot', '')) and m['animal'] in animal_ids]
        known = {(d['group'], d.get('lot', ''), animal) for d, dataset in zip(cfg['datasets'], self.project.datasets)
                 for animal in dataset.animals['Animal']}
        cfg['selection']['exclude_mice'] = [m for m in excluded if (m['group'], m['lot'], m['animal']) in known]
        cfg['selection']['exclude_mice'].extend(m for m in renamed if m not in cfg['selection']['exclude_mice'])
        project = await self.task(Project, cfg)
        if project:
            self.metadata_dialog.close()
            await self.install(project)

    @staticmethod
    def view_for(analysis):
        if analysis.get('table') in ('activity_periods', 'activity_light_dark') or analysis.get('plot') == 'period_timecourse': return 'day / night'
        if analysis.get('table') in ('activity_hourly', 'activity_elapsed', 'activity_circadian', 'gate_hourly'): return 'hourly activity'
        return 'overview'

    def sections(self):
        return list(dict.fromkeys(a.get('section', 'Analysis') for a in self.project.analyses if self.view_for(a) == self.analysis_view.value))

    def update_windows_visibility(self):
        self.window_settings.set_visibility(self.analysis_view.value == 'day / night')

    def choose_view(self):
        if not self.project or self.updating_choices: return
        self.updating_choices = True
        try:
            sections = self.sections()
            self.section.set_options(sections, value=next(iter(sections), None))
            self.graph_choices()
        finally:
            self.updating_choices = False
        self.update_windows_visibility()
        return self.show_graph()

    def change_workspace(self):
        if not self.project or self.updating_choices: return
        return self.show_graph()

    def show_table(self):
        if not self.project or self.updating_choices or not self.table_select.value: return
        previous = getattr(self, 'table_preview', None)
        state = previous.state() if previous and previous.name == self.table_select.value else None
        self.table_content.clear()
        table = select_data(self.project.tables[self.table_select.value], {}, self.project.groups, self.project.selection)
        with self.table_content: self.table_preview = TablePreview(table, self.table_select.value, state)

    def open_board_graph(self, name):
        analysis = next(a for a in self.project.analyses if analysis_name(a) == name)
        self.updating_choices = True
        try:
            self.analysis_view.set_value(self.view_for(analysis))
            self.section.set_options(self.sections(), value=analysis.get('section', 'Analysis'))
            self.graph_choices(name)
            self.workspace_tabs.set_value('graphs')
        finally:
            self.updating_choices = False
        self.update_windows_visibility()
        return self.show_graph()

    def graph_choices(self, preferred=None):
        options = {analysis_name(a): a['title'] for a in self.project.analyses
                   if a.get('section', 'Analysis') == self.section.value and self.view_for(a) == self.analysis_view.value}
        self.name = preferred if preferred in options else next(iter(options), None)
        self.graph_select.set_options(options, value=self.name)
        self.add_graph_button.set_enabled(bool(self.name))

    def choose_section(self):
        if not self.project or self.updating_choices: return
        self.updating_choices = True
        try:
            self.graph_choices()
        finally:
            self.updating_choices = False
        return self.show_graph()

    def choose_graph(self):
        if not self.project or self.updating_choices: return
        self.name = self.graph_select.value
        return self.show_graph()

    async def show_graph(self):
        self.render_version += 1
        version = self.render_version
        async with self.render_lock:
            if version != self.render_version or not self.project or self.busy: return
            project, name = self.project, self.name
            if self.workspace_tabs.value == 'tables':
                self.show_table()
                return
            if self.workspace_tabs.value == 'page':
                self.board.prepare()
                names = [p['name'] for p in self.board.panels]
                results = await self.task(lambda: {name: project.result(name) for name in names}, status='calculating the graphs on dashboard…')
                if version == self.render_version and project is self.project: self.board.render(project, results or {})
                return
            if not name:
                self.graph_area.clear(); self.editor_area.clear(); self.details_area.clear()
                with self.graph_area: ui.label('No graphs in this category for these exports.').classes('muted')
                return
            result = await self.task(project.result, name, status='calculating the selected graph…')
            # an older calculation must not replace a newer view
            if version != self.render_version or project is not self.project or name != self.name: return
            self.draw_graph(project, name, result)

    def draw_graph(self, project, name, result):
        self.graph_area.clear(); self.editor_area.clear(); self.details_area.clear()
        if result is None:
            with self.graph_area: ui.label('No graph for this selection. Choose other mice, groups, or windows.').classes('muted')
            return
        with self.graph_area:
            with ui.card().classes('w-full p-2'):
                figure = result['figure'].to_plotly_json()
                figure['config'] = PLOT_CONFIG
                plot = ui.plotly(figure).classes('w-full').mark('plot')
                plot.on('plotly_relayout', lambda e: project.edit_layout(name, e.args))
                plot.on('plotly_restyle', lambda e: project.edit_traces(name, e.args))
                with ui.row().classes('w-full px-4'):
                    ui.button('HTML', on_click=lambda: self.download_graph('html')).props('flat dense')
                    ui.button('PNG', on_click=lambda: self.download_graph('png')).props('flat dense')
                    ui.button('SVG', on_click=lambda: self.download_graph('svg')).props('flat dense')
                    ui.button('graph data · CSV', on_click=lambda: ui.download.content(result['data'].write_csv(), f'{name}.csv')).props('flat dense')
                    ui.button('Prism CSV + mouse IDs', on_click=lambda: self.download_prism(name, result)).props('flat dense').mark('prism-csv')
                    ui.space()
                    ui.label(f'{result["data"].height} observations · drag, zoom, edit labels').classes('muted')
        with self.editor_area:
            with ui.expansion('edit this graph', icon='palette').classes('w-full bg-white rounded-xl'):
                with ui.row().classes('w-full'):
                    title = ui.input('title', value=result['figure'].layout.title.text).classes('grow').mark('chart-title')
                    xlabel = ui.input('x axis', value=result['figure'].layout.xaxis.title.text or '').classes('grow')
                    ylabel = ui.input('y axis', value=result['figure'].layout.yaxis.title.text or '').classes('grow')
                    legend = ui.switch('show legend', value=result['figure'].layout.showlegend is not False)
                async def apply_titles():
                    project.edit_layout(name, {'title.text': title.value, 'xaxis.title.text': xlabel.value,
                                                   'yaxis.title.text': ylabel.value, 'showlegend': legend.value})
                    await self.show_graph()
                ui.button('apply titles', on_click=apply_titles).props('flat').mark('apply-titles')
                with ui.row().classes('w-full'):
                    for group in project.selection['groups']:
                        trace = next((t for t in result['figure'].data if isinstance(t.meta, dict) and t.meta.get('group') == group), None)
                        if trace is None: continue
                        with ui.column():
                            label = ui.input(f'legend · {group}', value=(trace.name or group).removesuffix(f' · {trace.meta.get("period")}') if trace.meta.get('period') else trace.name or group)
                            color = ui.color_input(f'color · {group}', value=colors_for(project.groups, project.cfg.get('colors'))[group])
                            async def apply_group(group=group, label=label, color=color):
                                fig = project.result(name)['figure']
                                for i, t in enumerate(fig.data):
                                    if isinstance(t.meta, dict) and t.meta.get('group') == group:
                                        legend_name = f'{label.value} · {t.meta["period"]}' if t.meta.get('period') else label.value
                                        patch = {'marker.color': color.value, 'name': legend_name}
                                        if t.type == 'scatter': patch['line.color'] = color.value
                                        if t.type == 'bar': patch['marker.line.color'] = color.value
                                        project.edit_traces(name, [patch, [i]])
                                await self.show_graph()
                            ui.button('apply', on_click=apply_group).props('flat dense').mark(f'apply-color-{group}')
                async def reset():
                    project.styles.pop(name, None)
                    await self.show_graph()
                ui.button('reset graph edits', on_click=reset).props('flat dense')
        with self.details_area:
            for note in result['notes']: ui.label(note).classes('muted')
            # build table previews only when requested
            with ui.expansion('statistics & graph data', icon='table_chart').classes('w-full').mark('statistics') as statistics:
                stats_content = ui.column().classes('w-full')
            def show_statistics(event):
                if not event.value or any(stats_content.descendants()): return
                with stats_content:
                    for kind, table in result['stats'].items():
                        ui.label(kind.replace('_', ' ')).classes('font-bold')
                        self.grid(table, f'{name}__{kind}')
                    self.grid(result['data'], name)
            statistics.on_value_change(show_statistics)
    @staticmethod
    def grid(table, name='table'):
        return TablePreview(table, name)

    def new_project(self, *, keep_labels=False):
        self.render_version += 1
        self.mice_open = False
        if self.import_dialog:
            self.import_dialog.close()
        if self.metadata_dialog:
            self.metadata_dialog.close()
        if not keep_labels: self.dataset_labels.clear()
        self.project = None
        self.show_folders()
        self.name = None
        self.pending_specs = None
        for area in (self.filters, self.sources, self.calculations, self.table_content, self.graph_area, self.editor_area, self.details_area):
            area.clear()
        self.board.clear()
        self.add_graph_button.disable()
        self.page_tab.set_label('dashboard')
        self.table_select.set_options({}, value=None)
        self.workspace_tabs.set_value('graphs')
        self.section.set_options([], value=None)
        self.graph_select.set_options({}, value=None)
        self.heading.set_text(APP_NAME)
        self.summary.set_text('Load your exports to begin.')

    async def upload(self, event):
        data = await event.file.read()
        destination = self.workspace / uuid4().hex
        if event.file.name.lower().endswith('.icage'):
            project = await self.task(Project.open, data, destination)
        else:
            def extract():
                unpack(data, destination)
                return discover(destination)
            specs = await self.task(extract, status='checking exports…')
            if specs: await self.add_specs(specs)
            return
        if project: await self.install(project, reset_labels=True)

    async def load_pending(self):
        if not self.pending_specs:
            ui.notify('Choose a folder and wait for the upload first', type='info'); return
        await self.add_specs(self.pending_specs)
        self.pending_specs = None

    async def add_specs(self, specs):
        cfg = self.project.configuration() if self.project else {'analyses': 'all', **copy.deepcopy(self.defaults)}
        datasets = cfg.setdefault('datasets', [])
        seen = {path for ds in datasets for path in source_paths(ds)}
        incoming = []
        for spec in specs:
            path = Path(spec['path']).expanduser().resolve()
            if path in seen: continue
            labels = self.dataset_labels.get(path)
            incoming.append({**copy.deepcopy(labels if labels is not None else spec), 'path': str(path)})
            seen.add(path)
        if not incoming:
            ui.notify('These folders are already in the project', type='info')
            return
        if any(not ds.get('group') or not ds.get('phase') for ds in incoming):
            hints = await self.task(lambda: [group_hint(Path(ds['path'])) for ds in incoming], status='reading dataset labels…')
            if hints is not None: self.ask_labels(incoming, hints)
            return
        datasets.extend(incoming)
        added = [ds['group'] for ds in incoming]
        present = {ds['group'] for ds in datasets}
        order = dict.fromkeys([ds['group'] for ds in self.dataset_labels.values()] + [ds['group'] for ds in datasets])
        cfg['groups'] = [group for group in order if group in present]
        if 'selection' in cfg:
            selected = set(cfg['selection']['groups'] + added)
            cfg['selection']['groups'] = [group for group in cfg['groups'] if group in selected]
        project = await self.task(Project, cfg, status='reading exports and calculating tables…')
        if project:
            await self.install(project)
            return True

    def ask_labels(self, specs, hints):
        if self.import_dialog: self.import_dialog.close()
        self.import_specs = specs
        self.import_label_inputs = []
        with ui.dialog().props('persistent') as self.import_dialog, ui.card().classes('w-[760px] max-w-full max-h-[85vh] overflow-y-auto'):
            ui.label('dataset labels').classes('text-xl font-bold')
            ui.label('Set the experimental group and phase. These labels are saved with your project.').classes('muted')
            for i, (ds, hint) in enumerate(zip(specs, hints)):
                ui.label(Path(ds['path']).name).classes('font-medium')
                ui.label(ds['path']).classes('muted break-all')
                if hint: ui.label(f'Group in Animal.txt: {hint} · confirm its experimental meaning.').classes('muted')
                with ui.row().classes('w-full'):
                    group = ui.input('group', value=ds.get('group') or hint).mark(f'import-group-{i}')
                    lot = ui.input('lot', value=ds.get('lot', '')).mark(f'import-lot-{i}')
                    phase = ui.input('phase', value=ds.get('phase', '')).mark(f'import-phase-{i}')
                self.import_label_inputs.append((group, lot, phase))
            with ui.row():
                ui.button('cancel', on_click=self.import_dialog.close).props('flat').mark('cancel-import')
                ui.button('add folders', on_click=self.confirm_import).mark('confirm-import')
        self.import_dialog.open()

    async def confirm_import(self):
        if self.busy: return
        specs = copy.deepcopy(self.import_specs)
        for spec, inputs in zip(specs, self.import_label_inputs):
            spec.update(zip(('group', 'lot', 'phase'), (field.value.strip() for field in inputs)))
            try:
                validate_labels(spec)
            except ValueError as exc:
                ui.notify(str(exc), type='warning')
                return
        if await self.add_specs(specs):
            self.import_dialog.close()

    async def add_folders(self, folders):
        def collect():
            specs = []
            for folder in folders:
                path = Path(folder).expanduser().resolve()
                if not path.is_dir():
                    raise ValueError(f'Folder not found: {path}')
                specs.extend(discover(path))
            return specs
        specs = await self.task(collect, status='checking selected folders…')
        if specs: await self.add_specs(specs)

    async def remove_folder(self, path):
        if not self.project or self.busy: return
        path = Path(path).expanduser().resolve()
        remaining = [ds for ds in self.project.datasets if ds.path.resolve() != path]
        if len(remaining) == len(self.project.datasets): return
        if not remaining:
            self.new_project(keep_labels=True)
            return
        cfg = self.project.configuration()
        cfg['datasets'] = [ds for ds in cfg['datasets'] if Path(ds['path']).expanduser().resolve() != path]
        groups = {ds.group for ds in remaining}
        phases = list(dict.fromkeys(ds.phase for ds in remaining))
        mice = {(ds.group, ds.lot, animal) for ds in remaining for animal in ds.animals['Animal']}
        cfg['colors'] = colors_for(self.project.groups, cfg.get('colors'))
        cfg['groups'] = [g for g in self.project.groups if g in groups]
        selected = self.project.selection['groups']
        cfg['selection']['groups'] = ([g for g in selected if g in groups] or cfg['groups']) if selected else []
        cfg['selection']['phases'] = [p for p in self.project.selection.get('phases', []) if p in phases] or phases[:1]
        cfg['selection']['exclude_mice'] = [m for m in self.project.selection.get('exclude_mice', [])
                                             if (m['group'], m['lot'], m['animal']) in mice]
        project = await self.task(Project, cfg, status='updating the remaining folders…')
        if project: await self.install(project)

    async def native_folder(self):
        if self.busy: return
        import webview
        folders = await app.native.main_window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=True)
        if folders:
            await self.add_folders(folders)

    async def native_project(self):
        if self.busy: return
        import webview
        paths = await app.native.main_window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=('IntelliCage project (*.icage)',))
        if not paths: return
        destination = self.workspace / uuid4().hex
        def load():
            return Project.open(Path(paths[0]).read_bytes(), destination)
        project = await self.task(load, status='opening project…')
        if project: await self.install(project, reset_labels=True)

    async def save_project(self):
        if not self.project: return
        data = await self.task(self.project.save)
        if data: ui.download.content(data, 'intellicage.icage', 'application/zip')

    async def export_all(self):
        if not self.project: return
        ui.notify('Preparing every graph, its data and statistics…', type='info')
        data = await self.task(self.project.export)
        if data: ui.download.content(data, 'intellicage-results.zip', 'application/zip')

    def download_prism(self, name, result):
        analysis = next(a for a in self.project.analyses if analysis_name(a) == name)
        table = analysis_csv(analysis, self.project.tables[analysis['table']],
                             self.project.selection.get('groups', self.project.groups), self.project.selection, result['data'])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for filename, content in table.files(name).items():
                archive.writestr(filename, content)
        ui.download.content(buffer.getvalue(), f'{name}-prism.zip', 'application/zip')

    async def download_graph(self, kind, name=None):
        name = name or self.name
        if not self.project or not name: return
        def create():
            figure = self.project.result(name)['figure']
            return figure.to_html(include_plotlyjs=True, config=PLOT_CONFIG) if kind == 'html' else figure.to_image(format=kind, scale=2)
        data = await self.task(create)
        if data: ui.download.content(data.encode() if isinstance(data, str) else data, f'{name}.{kind}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--desktop', action='store_true', help=argparse.SUPPRESS)
    mode.add_argument('--browser', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.yaml')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--no-show', action='store_true')
    args = parser.parse_args(argv)
    desktop = not args.browser
    port = args.port if args.port is not None else native.find_open_port()
    if desktop:
        app.native.settings['ALLOW_DOWNLOADS'] = True
    config = args.config if args.config.is_file() else None
    if config:
        cfg = yaml.safe_load(config.read_text())
        if not all((Path(d['path']).expanduser() if Path(d['path']).expanduser().is_absolute()
                    else config.parent / d['path']).is_dir() for d in cfg.get('datasets', [])):
            config = None
    @ui.page('/')
    def index():
        Explorer(config, local=True, native=desktop)
    ui.run(host='127.0.0.1', port=port, native=desktop,
           title=APP_NAME, window_size=(1440, 960) if desktop else None,
           reload=False, show=not args.no_show, show_welcome_message=False)
