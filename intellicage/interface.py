from __future__ import annotations

import argparse
import copy
import io
import zipfile
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from nicegui import app, run, ui
import yaml
import polars as pl

from .catalogue import analysis_name
from .plots import PLOT_CONFIG, colors_for
from .project import MAX_FILES, MAX_UPLOAD, Project, discover, safe_name, unpack
from .prism import analysis_csv
from .selection import select_data
from .timebins import period_windows

ROOT = Path(__file__).resolve().parents[1]
IMPORTS = {}


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
                if not name.lower().endswith('.txt'):
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


class Explorer:
    def __init__(self, config: Path | None, local: bool, native: bool):
        self.config, self.local, self.native = config, local, native
        self.project = None
        self.pending_specs = None
        self.name = None
        self.busy = False
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

    async def task(self, function, *args):
        if self.busy:
            return None
        self.busy = True
        self.progress.set_visibility(True)
        try:
            return await run.io_bound(function, *args)
        except Exception as exc:
            ui.notify(str(exc), type='negative', close_button=True, timeout=10000)
            return None
        finally:
            self.busy = False
            self.progress.set_visibility(False)

    async def initial_load(self):
        def load():
            cfg = yaml.safe_load(self.config.read_text())
            for dataset in cfg['datasets']:
                path = Path(dataset['path']).expanduser()
                dataset['path'] = str(path if path.is_absolute() else self.config.parent / path)
            return Project(cfg)
        project = await self.task(load)
        if project:
            self.install(project)

    def build(self):
        ui.colors(primary='#327c73', secondary='#d68a58', accent='#327c73')
        ui.add_css('''body{background:#f5f5f0;color:#223c37}.q-card{border:1px solid #e2e6de;box-shadow:none;border-radius:14px}
.nicegui-content{max-width:1500px;margin:auto;padding:28px;width:100%}.q-drawer{background:#fcfcf8}
h1,h2{letter-spacing:-.025em}.plotly-graph-div{border-radius:12px}.muted{color:#6e7e76;font-size:13px}''')
        with ui.header().classes('bg-white text-primary border-b items-center px-6'):
            ui.button(icon='menu', on_click=lambda: self.drawer.toggle()).props('flat round')
            ui.label('intelli / cage').classes('text-xl font-bold')
            ui.label('behaviour, mouse by mouse').classes('muted ml-4')
            ui.space()
            ui.button('new project', icon='add', on_click=self.new_project).props('flat').mark('new-project')
            ui.button('save project', icon='save', on_click=self.save_project).props('flat').mark('save-project')
            ui.button('export all', icon='download', on_click=self.export_all).mark('export-all')
        self.progress = ui.linear_progress().props('indeterminate').classes('w-full')
        self.progress.set_visibility(False)
        with ui.left_drawer(value=True).classes('p-5').props('width=300') as self.drawer:
            ui.label('your data').classes('text-xl font-bold')
            ui.label('Choose an IntelliCage export folder, ZIP, or saved .icage project.').classes('muted')
            # relative paths preserve the animal gate subfolder
            ui.html(f'''<label style="display:block;padding:12px;background:#e7efea;border-radius:8px;cursor:pointer">
choose export folder<input style="display:none" type="file" webkitdirectory multiple onchange="
const status=document.getElementById('folder-status-{self.token}');
status.textContent='uploading…';const form=new FormData();
for(const f of this.files){{if(f.name.toLowerCase().endsWith('.txt'))form.append('files',f,f.webkitRelativePath);}}
fetch('/imports/{self.token}',{{method:'POST',body:form}}).then(async r=>{{const v=await r.json();
status.textContent=r.ok?v.datasets+' exports ready — click load folder':v.detail;}}).catch(e=>status.textContent=e.message);"></label><small id="folder-status-{self.token}"></small>''', sanitize=False)
            ui.button('load folder', on_click=self.load_pending).props('flat').classes('w-full').mark('load-folder')
            ui.upload(label='ZIP / saved project', auto_upload=True, max_file_size=MAX_UPLOAD,
                      on_upload=self.upload, on_rejected=lambda: ui.notify('File exceeds the upload limit', type='negative')).props('accept=.zip,.icage').classes('w-full')
            if self.local:
                if self.native:
                    ui.button('choose local folder', on_click=self.native_folder).props('flat')
                with ui.expansion('local folder path').classes('w-full'):
                    self.path_input = ui.input('folder path').classes('w-full')
                    ui.button('add folder', on_click=self.local_folder).props('flat')
            ui.separator().classes('my-4')
            self.filters = ui.column().classes('w-full gap-3')
            with ui.expansion('how the numbers work').classes('w-full'):
                ui.label('Each dot is one mouse. Bars show group means with SEM or SD. Time and lick durations are in seconds.').classes('muted')
                ui.label('Rates use recorded observation time. Day/night windows can leave gaps. Events belong to the window where they start. Whole gate sessions belong to their starting hour.').classes('muted')
                ui.label('Learning uses correct and incorrect nosepokes. Open triangles mark mice without a visit in the latency window; tests include them as censored observations.').classes('muted')
                ui.label('Hourly curves align to phase start; clock-hour profiles use local export time. Daily learning uses 24-hour blocks from phase start. Day/night cycles use the date a window starts.').classes('muted')
        self.heading = ui.label('Explore IntelliCage behaviour').classes('text-3xl font-bold')
        self.summary = ui.label('Load your exports to begin.').classes('muted')
        self.sources = ui.column().classes('w-full gap-2')
        with ui.row().classes('w-full items-end gap-4'):
            self.section = ui.select([], label='section', on_change=self.choose_section).classes('w-52').mark('section')
            self.graph_select = ui.select({}, label='graph', with_input=True, on_change=self.choose_graph).classes('grow').mark('graph')
        self.graph_area = ui.column().classes('w-full')
        self.editor_area = ui.column().classes('w-full')
        self.details_area = ui.column().classes('w-full')

    def install(self, project):
        self.project = project
        self.name = analysis_name(project.analyses[0]) if project.analyses else None
        self.heading.set_text(project.cfg.get('run_name', 'IntelliCage analysis'))
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
        sections = list(dict.fromkeys(a.get('section', 'Analysis') for a in project.analyses))
        self.section.set_options(sections, value=sections[0] if sections else None)
        self.choose_section()

    def build_filters(self):
        p = self.project
        self.filters.clear()
        with self.filters:
            ui.label('selection').classes('text-lg font-bold')
            self.group_select = ui.select(p.groups, value=p.selection['groups'], multiple=True, label='groups').classes('w-full').mark('groups')
            self.phase_select = ui.select(p.phases, value=p.selection['phases'][0] if p.selection['phases'] else None, label='phase').classes('w-full')
            mice = p.tables['mouse_summary'].select('group', 'lot', 'animal').unique().sort('group', 'lot', 'animal').to_dicts()
            self.mouse_keys = mice
            excluded = p.selection.get('exclude_mice', [])
            self.mouse_select = ui.select({i: f'{m["animal"]} · {m["group"]} · {m["lot"]}' for i, m in enumerate(mice)},
                value=[i for i, m in enumerate(mice) if m in excluded], multiple=True, with_input=True, label='exclude mice').classes('w-full').mark('exclude-mice')
            ui.button('apply selection', on_click=self.apply_selection).classes('w-full').mark('apply-selection')
            with ui.expansion('calculation settings').classes('w-full'):
                windows = period_windows(p.cfg['settings'])
                self.window_inputs = []
                for w in windows:
                    name = ui.input('window name', value=w['name']).classes('w-full')
                    start = ui.input('from · HH:MM', value=f'{w["start"] // 60:02}:{w["start"] % 60:02}').classes('w-full')
                    end = ui.input('until · HH:MM', value=f'{w["end"] // 60:02}:{w["end"] % 60:02}').classes('w-full')
                    self.window_inputs.append((name, start, end))
                ui.label('Day is paired with the night starting on the same date. Default windows: 08:00–20:00 / 20:00–08:00.').classes('muted')
                self.partial = ui.switch('include partial windows', value=p.cfg['settings']['include_partial_periods'])
                self.paired = ui.switch('paired gate records only', value=p.cfg['settings']['gate_complete_only'])
                self.latency = ui.number('first visit observation window · minutes', value=p.cfg['settings']['latency_window_min'], min=.1).mark('first-visit-window')
                ui.label('Leave empty for the full phase. Applies to latency and first visit duration.').classes('muted')
                self.min_day = ui.number('minimum learning day · hours', value=p.cfg['settings']['min_day_hours'], min=.1, max=24)
                self.error = ui.select(['sem', 'sd'], label='error bars', value=p.cfg['settings']['error'])
                self.bar_style = ui.select(['bar', 'scatter'], label='summary style', value=p.cfg['settings']['bar_style'])
                self.test = ui.select(['welch', 'student', 'mann_whitney', 'auto'], label='group test', value=p.cfg['settings']['test'])
                self.repeated = ui.select(['mixed_anova', 'lmm'], label='repeated measures', value=p.cfg['settings']['repeated_test'])
                self.adjust = ui.select(['sidak', 'holm', 'bonferroni', 'fdr_bh'], label='multiple comparisons', value=p.cfg['settings']['padjust'])
                ui.button('recalculate', on_click=self.recalculate).classes('w-full').mark('recalculate')
            with ui.expansion('dataset labels').classes('w-full'):
                self.label_inputs = []
                for ds in p.cfg['datasets']:
                    ui.label(Path(ds['path']).name).classes('muted')
                    self.label_inputs.append((ui.input('group', value=ds['group']), ui.input('lot', value=ds.get('lot', '')), ui.input('phase', value=ds.get('phase', ''))))
                ui.button('apply labels', on_click=self.relabel).props('flat')

    async def apply_selection(self):
        if not self.group_select.value or not self.phase_select.value:
            ui.notify('Select a group and a phase', type='warning'); return
        self.project.set_selection({'groups': self.group_select.value, 'phases': [self.phase_select.value],
                                   'exclude_mice': [self.mouse_keys[i] for i in self.mouse_select.value]})
        await self.show_graph()

    async def recalculate(self):
        settings = {'period_windows': [{'name': n.value, 'start': s.value, 'end': e.value} for n, s, e in self.window_inputs],
                    'include_partial_periods': self.partial.value, 'gate_complete_only': self.paired.value,
                    'latency_window_min': self.latency.value, 'min_day_hours': self.min_day.value,
                    'error': self.error.value, 'bar_style': self.bar_style.value, 'test': self.test.value, 'repeated_test': self.repeated.value, 'padjust': self.adjust.value}
        def rebuild():
            self.project.rebuild(settings)
            return True
        if await self.task(rebuild):
            await self.show_graph()

    async def relabel(self):
        cfg = self.project.configuration()
        cfg.pop('groups', None); cfg.pop('selection', None); cfg.pop('styles', None)
        for ds, (group, lot, phase) in zip(cfg['datasets'], self.label_inputs):
            ds.update(group=group.value.strip(), lot=lot.value.strip(), phase=phase.value.strip())
            if not ds['group'] or not ds['phase']:
                ui.notify('Group and phase names are required', type='warning'); return
        project = await self.task(Project, cfg)
        if project: self.install(project)

    def choose_section(self):
        if not self.project: return
        options = {analysis_name(a): a['title'] for a in self.project.analyses if a.get('section', 'Analysis') == self.section.value}
        self.graph_select.set_options(options, value=next(iter(options), None))

    async def choose_graph(self):
        self.name = self.graph_select.value
        await self.show_graph()

    async def show_graph(self):
        if not self.project or not self.name or self.busy: return
        name = self.name
        result = await self.task(self.project.result, name)
        if name != self.name: return
        self.graph_area.clear(); self.editor_area.clear(); self.details_area.clear()
        if result is None:
            with self.graph_area: ui.label('No graph for this selection. Choose other mice, groups, or windows.').classes('muted')
            return
        with self.graph_area:
            with ui.card().classes('w-full p-2'):
                figure = result['figure'].to_plotly_json()
                figure['config'] = PLOT_CONFIG
                plot = ui.plotly(figure).classes('w-full').mark('plot')
                plot.on('plotly_relayout', lambda e: self.project.edit_layout(name, e.args))
                plot.on('plotly_restyle', lambda e: self.project.edit_traces(name, e.args))
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
                    self.project.edit_layout(name, {'title.text': title.value, 'xaxis.title.text': xlabel.value,
                                                   'yaxis.title.text': ylabel.value, 'showlegend': legend.value})
                    await self.show_graph()
                ui.button('apply titles', on_click=apply_titles).props('flat').mark('apply-titles')
                with ui.row().classes('w-full'):
                    for group in self.project.selection['groups']:
                        trace = next((t for t in result['figure'].data if isinstance(t.meta, dict) and t.meta.get('group') == group), None)
                        if trace is None: continue
                        with ui.column():
                            label = ui.input(f'legend · {group}', value=(trace.name or group).removesuffix(f' · {trace.meta.get("period")}') if trace.meta.get('period') else trace.name or group)
                            color = ui.color_input(f'color · {group}', value=colors_for(self.project.groups, self.project.cfg.get('colors'))[group])
                            async def apply_group(group=group, label=label, color=color):
                                fig = self.project.result(name)['figure']
                                for i, t in enumerate(fig.data):
                                    if isinstance(t.meta, dict) and t.meta.get('group') == group:
                                        legend_name = f'{label.value} · {t.meta["period"]}' if t.meta.get('period') else label.value
                                        patch = {'marker.color': color.value, 'name': legend_name}
                                        if t.type == 'scatter': patch['line.color'] = color.value
                                        if t.type == 'bar': patch['marker.line.color'] = color.value
                                        self.project.edit_traces(name, [patch, [i]])
                                await self.show_graph()
                            ui.button('apply', on_click=apply_group).props('flat dense').mark(f'apply-color-{group}')
                async def reset():
                    self.project.styles.pop(name, None)
                    await self.show_graph()
                ui.button('reset graph edits', on_click=reset).props('flat dense')
        with self.details_area:
            for note in result['notes']: ui.label(note).classes('muted')
            with ui.expansion('statistics & graph data', icon='table_chart').classes('w-full'):
                for kind, table in result['stats'].items():
                    ui.label(kind.replace('_', ' ')).classes('font-bold')
                    self.grid(table)
                    ui.button('download CSV', on_click=lambda table=table, kind=kind: ui.download.content(table.write_csv(), f'{name}__{kind}.csv')).props('flat dense')
                self.grid(result['data'])
            with ui.expansion('all intermediate tables', icon='dataset').classes('w-full'):
                for table_name, table in self.project.tables.items():
                    selected = select_data(table, {}, self.project.groups, self.project.selection)
                    ui.button(f'{table_name} · {selected.height} rows · CSV', on_click=lambda table=selected, table_name=table_name: ui.download.content(table.write_csv(), f'{table_name}.csv')).props('flat dense')

    @staticmethod
    def grid(table):
        # cap the preview, keep every row in the download
        rows = json.loads(table.head(200).write_json())
        ui.aggrid({'columnDefs': [{'field': c, 'sortable': True, 'filter': True, 'resizable': True} for c in table.columns],
                   'rowData': rows, 'pagination': True, 'paginationPageSize': 10}).classes('w-full h-64')

    def new_project(self):
        self.project = None
        self.name = None
        self.pending_specs = None
        for area in (self.filters, self.sources, self.graph_area, self.editor_area, self.details_area):
            area.clear()
        self.section.set_options([], value=None)
        self.graph_select.set_options({}, value=None)
        self.heading.set_text('Explore IntelliCage behaviour')
        self.summary.set_text('Load your exports to begin.')

    async def upload(self, event):
        data = await event.file.read()
        destination = self.workspace / uuid4().hex
        if event.file.name.lower().endswith('.icage'):
            project = await self.task(Project.open, data, destination)
        else:
            def load():
                unpack(data, destination)
                return Project({'datasets': discover(destination), 'analyses': 'all'})
            project = await self.task(load)
        if project: self.install(project)

    async def load_pending(self):
        if not self.pending_specs:
            ui.notify('Choose a folder and wait for the upload first', type='info'); return
        await self.add_specs(self.pending_specs)
        self.pending_specs = None

    async def add_specs(self, specs):
        cfg = self.project.configuration() if self.project else {'analyses': 'all'}
        cfg['datasets'] = cfg.get('datasets', []) + specs
        cfg.pop('groups', None); cfg.pop('selection', None)
        project = await self.task(Project, cfg)
        if project: self.install(project)

    async def local_folder(self):
        path = Path(self.path_input.value or '').expanduser()
        if not path.is_dir():
            ui.notify('Folder not found', type='warning'); return
        specs = await self.task(discover, path)
        if specs: await self.add_specs(specs)

    async def native_folder(self):
        import webview
        folders = await run.io_bound(app.native.main_window.create_file_dialog, webview.FileDialog.FOLDER)
        if folders:
            self.path_input.set_value(folders[0])
            await self.local_folder()

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

    async def download_graph(self, kind):
        if not self.project or not self.name: return
        name = self.name
        def create():
            figure = self.project.result(name)['figure']
            return figure.to_html(include_plotlyjs=True, config=PLOT_CONFIG) if kind == 'html' else figure.to_image(format=kind, scale=2)
        data = await self.task(create)
        if data: ui.download.content(data.encode() if isinstance(data, str) else data, f'{name}.{kind}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='IntelliCage explorer')
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--web', action='store_true', help='accept remote connections')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.yaml')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8080)))
    parser.add_argument('--no-show', action='store_true')
    args = parser.parse_args(argv)
    if args.desktop and args.web: parser.error('Choose desktop or web mode')
    config = args.config if args.config.is_file() else None
    if config:
        cfg = yaml.safe_load(config.read_text())
        if not all((Path(d['path']).expanduser() if Path(d['path']).expanduser().is_absolute()
                    else config.parent / d['path']).is_dir() for d in cfg.get('datasets', [])):
            config = None
    @ui.page('/')
    def index():
        Explorer(config, local=not args.web, native=args.desktop)
    ui.run(host='0.0.0.0' if args.web else '127.0.0.1', port=args.port, native=args.desktop,
           title='IntelliCage explorer', window_size=(1440, 960) if args.desktop else None,
           reload=False, show=not args.no_show, show_welcome_message=False)
