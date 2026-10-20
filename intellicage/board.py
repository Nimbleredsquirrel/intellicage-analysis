from __future__ import annotations

import math

from nicegui import ui

from .plots import PLOT_CONFIG
from .catalogue import analysis_name


class GraphBoard:
    def __init__(self, owner):
        self.owner, self.cards, self.plots = owner, {}, {}
        ui.label('my page').classes('text-lg font-bold')
        ui.label('Add graphs from the graphs tab. Drag by the handle to reorder; drag the bottom-right corner to resize.').classes('muted')
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
        self.owner.page_tab.set_label(f'my page ({len(valid)})' if valid else 'my page')

    def clear(self):
        self.container.clear()
        self.cards, self.plots = {}, {}
        with self.container: ui.label('No graphs yet. Use “add to my page” on a graph.').classes('muted')

    def add(self, name):
        if not self.owner.project or not name: return
        if any(p['name'] == name for p in self.panels):
            ui.notify('This graph is already on my page', type='info')
            return
        self.panels.append({'name': name, 'width': 480, 'height': 520})
        self.owner.page_tab.set_label(f'my page ({len(self.panels)})')
        ui.notify('Added to my page', type='positive')

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
        self.cards, self.plots = {}, {}
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
                        ui.label(title or name).classes('grow text-sm truncate')
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
                    plot.on('plotly_relayout', lambda e, name=name: project.edit_layout(name, e.args))
                    plot.on('plotly_restyle', lambda e, name=name: project.edit_traces(name, e.args))
                    self.plots[name] = plot
