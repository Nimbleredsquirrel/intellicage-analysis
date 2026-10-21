from __future__ import annotations

import math

from nicegui import ui

from .plots import PLOT_CONFIG
from .catalogue import analysis_name


class GraphBoard:
    def __init__(self, owner):
        self.owner, self.cards, self.plots = owner, {}, {}
        self.checkboxes, self.titles, self.badges = {}, {}, {}
        ui.label('dashboard').classes('text-lg font-bold')
        ui.label('Choose graphs below. Drag by the handle to reorder; drag the bottom-right corner to resize.').classes('muted')
        ui.label('Your title, legend and colour edits follow each graph. Removing a graph keeps its edits.').classes('muted text-sm')
        with ui.expansion('add / remove graphs', icon='checklist', value=True).classes('w-full') as self.picker:
            with ui.row().classes('w-full items-center'):
                self.search = ui.input('search graphs', on_change=lambda: self.refresh_picker()).props('clearable dense').classes('grow').mark('dashboard-search')
                self.edited_only = ui.checkbox('edited only', on_change=lambda: self.refresh_picker()).mark('dashboard-edited-only')
            self.choices = ui.column().classes('w-full gap-1').style('max-height:35vh;overflow-y:auto;display:block')

        self.container = ui.row().classes('w-full items-start gap-4').mark('graph-board')
        self.sortable = self.container.make_sortable(handle='.chart-drag-handle', on_end=self.reordered)
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
        names = {analysis_name(a) for a in self.owner.project.analyses}
        valid = []
        for panel in self.panels:
            if not isinstance(panel, dict) or panel.get('name') not in names or any(p['name'] == panel['name'] for p in valid): continue
            item = {'name': panel['name']}
            for key, default, minimum in [('width', 480, 300), ('height', 520, 300)]:
                value = panel.get(key, default)
                item[key] = max(minimum, min(4000, int(value))) if isinstance(value, (int, float)) and math.isfinite(value) else default
            valid.append(item)
        self.owner.project.cfg['workspace'] = {'charts': valid}
        self.owner.page_tab.set_label(f'dashboard ({len(valid)})' if valid else 'dashboard')
        self.refresh_picker()

    def clear(self):
        self.container.clear()
        self.cards, self.plots, self.titles, self.badges = {}, {}, {}, {}
        self.refresh_picker()
        with self.container: ui.label('No graphs yet. Choose graphs from the list above.').classes('muted')

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
        selected = {p['name'] for p in self.panels}
        self.picker.props(f'caption="{len(selected)} selected"')
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
            if not sections: ui.label('No matching graphs.').classes('muted')
            for section, entries in sections.items():
                ui.label(section).classes('text-sm font-semibold mt-3')
                for name, title, edited in entries:
                    with ui.row().classes('w-full items-center no-wrap gap-2'):
                        self.checkboxes[name] = ui.checkbox(title, value=name in selected,
                            on_change=lambda e, name=name: self.toggle(name, e.value)).classes('grow').mark(f'dashboard-choice-{name}')
                        ui.badge('edited' if edited else 'default', color='primary' if edited else 'grey').props('outline').mark(f'dashboard-status-{name}')

    async def toggle(self, name, included):
        if not self.owner.project: return
        if included: self.add(name)
        else:
            self.owner.project.cfg['workspace']['charts'] = [p for p in self.panels if p['name'] != name]
            self.prepare()
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
        self.panels.append({'name': name, 'width': 480, 'height': 520})
        self.owner.page_tab.set_label(f'dashboard ({len(self.panels)})')
        self.refresh_picker()
        ui.notify('Added to dashboard', type='positive')

    def remove(self, name):
        self.owner.project.cfg['workspace']['charts'] = [p for p in self.panels if p['name'] != name]
        self.prepare()
        return self.owner.show_graph()

    def reordered(self, _):
        order = {card.id: index for index, card in enumerate(self.container.default_slot.children)}
        self.panels.sort(key=lambda p: order.get(self.cards[p['name']].id, len(order)))

    def resized(self, name, size):
        if not self.owner.project: return
        panel = next((p for p in self.panels if p['name'] == name), None)
        if panel is None: return
        for key in ('width', 'height'):
            value = size.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 300:
                panel[key] = min(4000, round(value))

    def render(self, project, results):
        self.container.clear()
        self.cards, self.plots, self.titles, self.badges = {}, {}, {}, {}
        self.refresh_picker()
        if not self.panels:
            self.clear()
            return
        with self.container:
            for panel in self.panels:
                name = panel['name']
                result = results.get(name)
                with ui.card().classes('chart-window p-3 gap-2').style(
                    f'width:{panel["width"]}px;height:{panel["height"]}px').mark(f'chart-window-{name}') as card:
                    self.cards[name] = card
                    with ui.row().classes('w-full items-center no-wrap shrink-0 gap-1'):
                        ui.icon('drag_indicator').classes('chart-drag-handle cursor-grab p-1').tooltip('drag to reorder')
                        title = result['figure'].layout.title.text if result else name
                        self.titles[name] = ui.label(title or name).classes('grow text-sm truncate')
                        self.badges[name] = ui.badge('edited').props('outline')
                        self.badges[name].set_visibility(self.is_edited(name))
                        ui.button(icon='edit', on_click=lambda name=name: self.owner.open_board_graph(name)).props('flat round dense').tooltip('edit graph')
                        ui.button(icon='download', on_click=lambda name=name: self.owner.download_graph('html', name)).props('flat round dense').tooltip('download HTML')
                        ui.button(icon='close', on_click=lambda name=name: self.remove(name)).props('flat round dense').tooltip('remove graph')
                    ui.element('q-resize-observer').props('debounce=250').on('resize', lambda e, name=name: self.resized(name, e.args), args=['width', 'height']).mark(f'board-size-{name}')
                    if result is None:
                        ui.label('Could not draw this graph.').classes('muted')
                        continue
                    figure = result['figure'].to_plotly_json()
                    figure['layout'].pop('height', None)
                    figure['layout'].pop('width', None)
                    figure['layout']['autosize'] = True
                    figure['config'] = {**PLOT_CONFIG, 'responsive': True}
                    plot = ui.plotly(figure).classes('w-full grow min-h-0').style('height:100%;min-height:0').mark(f'board-plot-{name}')
                    plot.on('plotly_relayout', lambda e, name=name: self.edited(project, name, 'layout', e.args))
                    plot.on('plotly_restyle', lambda e, name=name: self.edited(project, name, 'traces', e.args))
                    self.plots[name] = plot
