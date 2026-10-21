from __future__ import annotations

import math
import copy
import json

from nicegui import ui

from .plots import PLOT_CONFIG
from .catalogue import analysis_name
from .dashboard_filters import DashboardFilters
from .dashboard_settings import DashboardSettings


class GraphBoard:
    def __init__(self, owner):
        self.owner, self.cards, self.plots = owner, {}, {}
        self.checkboxes, self.titles, self.badges = {}, {}, {}
        self.draft = None
        self.auto_height = set()
        self.selection_project = None
        with ui.row().classes('w-full items-center gap-3'):
            ui.label('dashboard').classes('text-lg font-bold')
            self.count = ui.label().classes('muted')
            ui.space()
            self.pdf_button = ui.button('PDF report', icon='picture_as_pdf', on_click=self.download_pdf).mark('dashboard-pdf').disable()
            ui.button('choose graphs', icon='add_chart', on_click=self.open_picker).mark('dashboard-choose')
        self.filters = DashboardFilters(owner)
        with ui.dialog() as self.picker:
            with ui.card().classes('w-full max-w-2xl gap-3').style('width:680px;max-height:85vh'):
                with ui.row().classes('w-full items-center'):
                    ui.label('choose graphs').classes('text-lg font-bold grow')
                    ui.button(icon='close', on_click=self.picker.close).props('flat round dense').tooltip('cancel')
                with ui.row().classes('w-full items-center'):
                    self.search = ui.input('search graphs', on_change=lambda: self.refresh_picker()).props('clearable outlined dense').classes('grow dashboard-selector').mark('dashboard-search')
                    self.edited_only = ui.checkbox('edited only', on_change=lambda: self.refresh_picker()).mark('dashboard-edited-only')
                self.choices = ui.column().classes('w-full gap-1').style('min-height:120px;max-height:50vh;overflow-y:auto;display:block')
                ui.separator()
                with ui.row().classes('w-full items-center justify-end'):
                    self.selection_count = ui.label().classes('muted mr-auto')
                    ui.button('cancel', on_click=self.picker.close).props('flat').mark('dashboard-cancel')
                    self.apply_button = ui.button('apply', on_click=self.apply_selection).mark('dashboard-apply')

        self.settings = DashboardSettings(self)
        self.container = ui.column().classes('w-full gap-6').mark('graph-board')
        self.group_rows, self.sortables, self.section_labels = {}, {}, {}
        self.clear()

    @property
    def panels(self):
        if not self.owner.project: return []
        workspace = self.owner.project.cfg.get('workspace')
        if not isinstance(workspace, dict):
            workspace = self.owner.project.cfg['workspace'] = {}
        if not isinstance(workspace.get('charts'), list): workspace['charts'] = []
        return workspace['charts']

    def prepare(self):
        if self.selection_project is not self.owner.project:
            self.picker.close()
            self.draft = None
        names = {analysis_name(a) for a in self.owner.project.analyses}
        valid = []
        for panel in self.panels:
            if not isinstance(panel, dict) or panel.get('name') not in names or any(p['name'] == panel['name'] for p in valid): continue
            item = {'name': panel['name']}
            for key, default, minimum in [('width', 480, 300), ('height', 650, 300)]:
                value = panel.get(key, default)
                item[key] = max(minimum, min(4000, int(value))) if isinstance(value, (int, float)) and math.isfinite(value) else default
            valid.append(item)
        self.owner.project.cfg['workspace'] = {'charts': valid}
        self.filters.sync()
        self.update_count()
        self.refresh_picker()

    def clear(self):
        self.settings.periods, self.settings.local, self.settings.local_areas = {}, {}, {}
        self.container.clear()
        self.group_rows, self.sortables, self.section_labels = {}, {}, {}
        self.cards, self.plots, self.titles, self.badges = {}, {}, {}, {}
        if self.owner.project is not self.selection_project:
            self.picker.close()
            self.draft = None
        self.filters.sync()
        self.settings.common(self.owner.project)
        self.update_count()
        self.refresh_picker()

    def is_edited(self, name):
        style = self.owner.project.styles.get(name, {})
        return bool(style.get('layout') or any(style.get('traces', {}).values()))

    def title(self, analysis):
        layout = self.owner.project.styles.get(analysis_name(analysis), {}).get('layout', {})
        title = layout.get('title', {})
        return layout.get('title.text', title.get('text') if isinstance(title, dict) else title) or analysis['title']

    def refresh_picker(self):
        self.choices.clear()
        self.checkboxes = {}
        project = self.owner.project
        selected = self.draft if self.picker.value and self.selection_project is project else {p['name'] for p in self.panels}
        self.selection_count.set_text(f'{len(selected)} selected')
        if not project: return
        query = (self.search.value or '').casefold().strip()
        sections = {}
        for analysis in project.analyses:
            name = analysis_name(analysis)
            title, edited = self.title(analysis), self.is_edited(name)
            if self.edited_only.value and not edited: continue
            section = f"{self.owner.view_for(analysis)} · {analysis.get('section', 'Analysis')}"
            if query and query not in f"{title} {analysis['title']} {name} {section}".casefold(): continue
            sections.setdefault(section, []).append((name, title, edited))
        with self.choices:
            for section, entries in sections.items():
                ui.label(section).classes('text-sm font-semibold mt-3')
                for name, title, edited in entries:
                    with ui.row().classes('w-full items-center no-wrap gap-2'):
                        self.checkboxes[name] = ui.checkbox(title, value=name in selected,
                            on_change=lambda e, name=name: self.select(name, e.value)).classes('grow').mark(f'dashboard-choice-{name}')
                        ui.badge('edited' if edited else 'default', color='primary' if edited else 'grey').props('outline').mark(f'dashboard-status-{name}')

    def update_count(self):
        count = len(self.panels)
        self.count.set_text(f'{count} graphs')
        self.pdf_button.set_enabled(bool(count))
        self.owner.page_tab.set_label(f'dashboard ({count})' if count else 'dashboard')

    def open_picker(self):
        if not self.owner.project:
            ui.notify('Add your data folders first', type='info')
            return
        self.selection_project = self.owner.project
        self.draft = {p['name'] for p in self.panels}
        self.picker.open()
        self.refresh_picker()

    def select(self, name, included):
        if self.selection_project is not self.owner.project or self.draft is None: return
        if included: self.draft.add(name)
        else: self.draft.discard(name)
        self.selection_count.set_text(f'{len(self.draft)} selected')

    async def apply_selection(self):
        project = self.owner.project
        if not project or project is not self.selection_project or self.draft is None: return
        if self.owner.busy: return
        # keep the order and sizes of existing cards
        panels = [p for p in self.panels if p['name'] in self.draft]
        existing = {p['name'] for p in panels}
        self.auto_height.update(self.draft - existing)
        panels.extend({'name': name, 'width': 480, 'height': 650}
                      for analysis in project.analyses
                      if (name := analysis_name(analysis)) in self.draft and name not in existing)
        project.cfg['workspace']['charts'] = panels
        self.draft = None
        self.picker.close()
        self.prepare()
        # rendering belongs to the page, never a checkbox that may be removed
        with self.container:
            await self.owner.show_graph()

    def edited(self, project, name, kind, event):
        if project is not self.owner.project or name not in self.plots: return
        if kind == 'layout': project.edit_layout(name, event)
        else: project.edit_traces(name, event)
        self.refresh_picker()
        analysis = next(a for a in project.analyses if analysis_name(a) == name)
        self.titles[name].set_text(self.title(analysis))
        self.badges[name].set_visibility(self.is_edited(name))

    def add(self, name):
        if not self.owner.project or not name: return
        if any(p['name'] == name for p in self.panels):
            ui.notify('This graph is already on dashboard', type='info')
            return
        self.panels.append({'name': name, 'width': 480, 'height': 650})
        self.auto_height.add(name)
        self.filters.sync()
        self.update_count()
        self.refresh_picker()
        ui.notify('Added to dashboard', type='positive')

    def remove(self, name):
        self.owner.project.cfg['workspace']['charts'] = [p for p in self.panels if p['name'] != name]
        self.prepare()
        return self.owner.show_graph()

    async def download_pdf(self):
        from .dashboard_report import dashboard_pdf

        project = self.owner.project
        if not project or not self.panels: return
        with self.container.client:
            async with self.owner.render_lock:
                if self.owner.project is not project: return
                order = {name: i for i, name in enumerate(self.plots)}
                panels = sorted(copy.deepcopy(self.panels), key=lambda p: order.get(p['name'], len(order)))
                figures = {name: copy.deepcopy(plot.figure) for name, plot in self.plots.items()}
                config = {**project.configuration(), 'analyses': copy.deepcopy(project.analyses)}
                from .selection import select_data
                config['report_mice'] = select_data(project.tables['mouse_summary'], {}, project.groups, project.selection).select('group', 'lot', 'animal').unique().sort('group', 'lot', 'animal').to_dicts()
                bands = [{'id': self.filters.area.html_id, 'kind': 'selection'},
                         {'id': self.settings.area.html_id, 'kind': 'statistics'}]
                for key, label in self.section_labels.items():
                    bands.append({'id': label.html_id, 'kind': 'heading', 'text': f'{key[0]} · {key[1]}'})
                for key, editor in self.settings.periods.items():
                    bands.append({'id': editor.container.html_id, 'kind': 'periods'})
                for key, area in self.settings.local_areas.items():
                    bands.append({'id': area.html_id, 'kind': 'local'})
                nodes = [{'name': name, 'card': card.html_id, 'plot': self.plots[name].html_id}
                         for name, card in self.cards.items() if name in self.plots]
                script = """const nodes = NODES; const bands = BANDS;
                const rect = id => {const e = document.getElementById(id); if (!e) throw Error('Dashboard is not ready');
                    const r = e.getBoundingClientRect(); return {x:r.left,y:r.top,width:r.width,height:r.height};};
                const cards = nodes.map(n => ({name:n.name, ...rect(n.card), plot:rect(n.plot)}));
                const labels = bands.map(b => ({...b, ...rect(b.id)})).filter(b => b.width > 0 && b.height > 0);
                const all = [...cards, ...labels]; const x = Math.min(...all.map(r=>r.x)); const y = Math.min(...all.map(r=>r.y));
                const width = Math.max(...all.map(r=>r.x+r.width))-x;
                return {width, cards:cards.map(r=>({...r,x:r.x-x,y:r.y-y,plot:{...r.plot,x:r.plot.x-x,y:r.plot.y-y}})),
                    bands:labels.map(r=>({...r,x:r.x-x,y:r.y-y}))};""".replace('NODES', json.dumps(nodes)).replace('BANDS', json.dumps(bands))
                try:
                    geometry = await ui.run_javascript(script, timeout=10)
                except (TimeoutError, RuntimeError) as exc:
                    ui.notify('Could not read the dashboard layout. Try exporting again.', type='negative')
                    return
                data = await self.owner.task(dashboard_pdf, config, panels, figures, geometry, status='creating PDF report…')
            if data: ui.download.content(data, 'intellicage-dashboard.pdf', 'application/pdf')

    def reordered(self, key, _):
        row = self.group_rows[key]
        order = {card.id: index for index, card in enumerate(row.default_slot.children)}
        names = {name for name, card in self.cards.items() if card.parent_slot.parent is row}
        sorted_panels = iter(sorted((p for p in self.panels if p['name'] in names), key=lambda p: order[self.cards[p['name']].id]))
        self.panels[:] = [next(sorted_panels) if p['name'] in names else p for p in self.panels]

    def resized(self, name, size, source=None):
        if not self.owner.project or (source is not None and self.cards.get(name) is not source): return
        panel = next((p for p in self.panels if p['name'] == name), None)
        if panel is None: return
        previous = (panel['width'], panel['height'])
        for key in ('width', 'height'):
            value = size.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 300:
                panel[key] = min(4000, round(value))
        if card := self.cards.get(name): card.style(f'width:{panel["width"]}px;height:{panel["height"]}px')
        if previous != (panel['width'], panel['height']) and name in self.plots:
            from .graph_layout import fit_plot
            fit_plot(self.plots[name], panel['width'] - 26, panel['height'] - 74,
                     self.owner.project.styles.get(name, {}).get('layout', {}))

    def render(self, project, results):
        self.container.clear()
        self.group_rows, self.sortables, self.section_labels = {}, {}, {}
        self.cards, self.plots, self.titles, self.badges = {}, {}, {}, {}
        self.refresh_picker()
        if not self.panels:
            self.clear()
            return
        self.settings.common(project)
        self.settings.periods, self.settings.local, self.settings.local_areas = {}, {}, {}
        definitions = {analysis_name(a): a for a in project.analyses}
        grouped = {}
        for panel in self.panels:
            analysis = definitions[panel['name']]
            key = (self.owner.view_for(analysis), analysis.get('section', 'Analysis'))
            grouped.setdefault(key, []).append(panel)
        with self.container:
            for key, panels in grouped.items():
                with ui.column().classes('w-full gap-3').mark('dashboard-section'):
                    self.section_labels[key] = ui.label(f'{key[0]} · {key[1]}').classes('text-xl font-bold')
                    self.settings.group(project, key, [definitions[p['name']] for p in panels])
                    with ui.row().classes('w-full items-start gap-4') as row:
                        self.group_rows[key] = row
                        self.sortables[key] = row.make_sortable(handle='.chart-drag-handle', on_end=lambda e, key=key: self.reordered(key, e))
                        row.mark(f'dashboard-row-{len(self.group_rows)}')
                        for panel in panels: self.draw_card(project, panel, results)

    def draw_card(self, project, panel, results):
        name = panel['name']
        result = results.get(name)
        if name in self.auto_height and result:
            import plotly.graph_objects as go
            from .graph_layout import KEY, fit_figure
            preview = go.Figure(result['figure'])
            defaults = (preview.layout.meta or {}).get(KEY, {})
            panel['width'] = min(4000, defaults.get('default_width', 480) + 26)
            panel['height'] = max(panel['height'], preview.layout.height + 74)
            fit_figure(preview, panel['width'] - 26, panel['height'] - 74)
            panel['height'] = max(panel['height'], min(4000, math.ceil(preview.layout.margin.t + preview.layout.margin.b + defaults.get('plot_height', 240) + 74)))
            self.auto_height.discard(name)
        with ui.card().classes('chart-window p-3 gap-2').style(
            f'width:{panel["width"]}px;height:{panel["height"]}px').mark(f'chart-window-{name}') as card:
            self.cards[name] = card
            with ui.row().classes('w-full items-center no-wrap shrink-0 gap-1'):
                ui.icon('drag_indicator').classes('chart-drag-handle cursor-grab p-1').tooltip('drag to reorder')
                from .graph_layout import KEY
                title = (result['figure'].layout.meta or {}).get(KEY, {}).get('title_source', result['figure'].layout.title.text) if result else name
                self.titles[name] = ui.label(title or name).classes('grow text-sm truncate')
                self.badges[name] = ui.badge('edited').props('outline')
                self.badges[name].set_visibility(self.is_edited(name))
                ui.button(icon='edit', on_click=lambda name=name: self.owner.open_board_graph(name)).props('flat round dense').tooltip('edit graph')
                ui.button(icon='download', on_click=lambda name=name: self.owner.download_graph('html', name)).props('flat round dense').tooltip('download HTML')
                ui.button(icon='close', on_click=lambda name=name: self.remove(name)).props('flat round dense').tooltip('remove graph')
            ui.element('q-resize-observer').props('debounce=250').on('resize', lambda e, name=name, card=card: self.resized(name, e.args, card), args=['width', 'height']).mark(f'board-size-{name}')
            if result is None:
                ui.label('Could not draw this graph.').classes('muted')
                return
            figure = result['figure'].to_plotly_json()
            figure['layout'].pop('height', None)
            figure['layout'].pop('width', None)
            figure['layout']['autosize'] = True
            figure['config'] = {**PLOT_CONFIG, 'responsive': True}
            plot = ui.plotly(figure).classes('w-full grow min-h-0').style('height:100%;min-height:0').mark(f'board-plot-{name}')
            plot.on('plotly_relayout', lambda e, name=name: self.edited(project, name, 'layout', e.args))
            plot.on('plotly_restyle', lambda e, name=name: self.edited(project, name, 'traces', e.args))
            self.plots[name] = plot
            from .graph_layout import fit_plot
            fit_plot(plot, panel['width'] - 26, panel['height'] - 74, project.styles.get(name, {}).get('layout', {}))
