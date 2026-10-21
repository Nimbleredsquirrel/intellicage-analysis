import math

from nicegui import ui

from .plots import colors_for


def draw_editor(owner, project, name, result, window):
    figure = result['figure']
    with ui.expansion('edit graph', icon='palette').classes('w-full bg-white rounded-xl'):
        with ui.row().classes('w-full'):
            title = ui.input('title', value=figure.layout.title.text).classes('grow').mark('chart-title')
            xlabel = ui.input('x axis', value=figure.layout.xaxis.title.text or '').classes('grow')
            ylabel = ui.input('y axis', value=figure.layout.yaxis.title.text or '').classes('grow')
            legend = ui.switch('show legend', value=figure.layout.showlegend is not False)
        async def apply_titles():
            project.edit_layout(name, {'title.text': title.value, 'xaxis.title.text': xlabel.value,
                                      'yaxis.title.text': ylabel.value, 'showlegend': legend.value})
            await owner.show_graph()
        ui.button('apply titles', on_click=apply_titles).props('flat').mark('apply-titles')
        ui.label('positions and text').classes('font-medium')
        ui.label('x: 0 left, 1 right. y: 0 bottom, 1 top. Legend positions may extend outside the plot.').classes('muted')
        layout = figure.layout
        values = {}
        fields = [('title.x', 'title · x', layout.title.x if layout.title.x is not None else .02, -1, 2),
                  ('title.y', 'title · y', layout.title.y if layout.title.y is not None else .95, 0, 1),
                  ('title.font.size', 'title font · px', layout.title.font.size or 18, 6, 72),
                  ('font.size', 'text font · px', layout.font.size or 14, 6, 48),
                  ('legend.font.size', 'legend font · px', layout.legend.font.size or 14, 6, 48),
                  ('xaxis.title.font.size', 'x title font · px', layout.xaxis.title.font.size or 14, 6, 48),
                  ('yaxis.title.font.size', 'y title font · px', layout.yaxis.title.font.size or 14, 6, 48),
                  ('xaxis.tickangle', 'x labels · degrees', layout.xaxis.tickangle or 0, -180, 180),
                  ('yaxis.tickangle', 'y labels · degrees', layout.yaxis.tickangle or 0, -180, 180),
                  ('xaxis.title.standoff', 'x title spacing · px', layout.xaxis.title.standoff or 15, 0, 200),
                  ('yaxis.title.standoff', 'y title spacing · px', layout.yaxis.title.standoff or 15, 0, 200),
                  ('legend.x', 'legend · x', layout.legend.x if layout.legend.x is not None else 0, -2, 3),
                  ('legend.y', 'legend · y', layout.legend.y if layout.legend.y is not None else -.22, -2, 3)]
        with ui.row().classes('w-full items-end gap-3'):
            for key, label, value, minimum, maximum in fields:
                values[key] = ui.number(label, value=value, min=minimum, max=maximum).props('outlined dense stack-label').classes('w-40 dashboard-selector').mark(f'edit-{key}')
            values['title.xanchor'] = ui.select(['left', 'center', 'right'], label='title anchor', value=layout.title.xanchor or 'left').props('outlined dense stack-label').classes('w-40')
            values['legend.xanchor'] = ui.select(['left', 'center', 'right', 'auto'], label='legend x anchor', value=layout.legend.xanchor or 'auto').props('outlined dense stack-label').classes('w-40')
            values['legend.yanchor'] = ui.select(['top', 'middle', 'bottom', 'auto'], label='legend y anchor', value=layout.legend.yanchor or 'auto').props('outlined dense stack-label').classes('w-40')
            values['legend.orientation'] = ui.select({'h': 'horizontal', 'v': 'vertical'}, label='legend direction', value=layout.legend.orientation or 'h').props('outlined dense stack-label').classes('w-40')
        with ui.row().classes('w-full items-end gap-3'):
            for side in ['l', 'r', 't', 'b']:
                values[f'margin.{side}'] = ui.number(f'margin · {dict(l="left", r="right", t="top", b="bottom")[side]}', value=getattr(layout.margin, side) or 0, min=0, max=500).props('outlined dense stack-label').classes('w-40')
        async def apply_positions():
            try: project.edit_layout(name, {key: control.value for key, control in values.items()})
            except ValueError as exc:
                ui.notify(str(exc), type='negative')
                return
            await owner.show_graph()
        ui.button('apply positions', on_click=apply_positions).props('flat').mark('apply-positions')
        with ui.row().classes('w-full'):
            for group in project.selection['groups']:
                trace = next((t for t in figure.data if isinstance(t.meta, dict) and t.meta.get('group') == group), None)
                if trace is None: continue
                with ui.column():
                    label = ui.input(f'legend · {group}', value=(trace.name or group).removesuffix(f' · {trace.meta.get("period")}') if trace.meta.get('period') else trace.name or group)
                    current_color = trace.marker.color if hasattr(trace, 'marker') and isinstance(trace.marker.color, str) else colors_for(project.groups, project.cfg.get('colors'))[group]
                    color = ui.color_input(f'color · {group}', value=current_color)
                    async def apply_group(group=group, label=label, color=color):
                        for i, t in enumerate(project.result(name)['figure'].data):
                            if isinstance(t.meta, dict) and t.meta.get('group') == group:
                                patch = {'marker.color': color.value, 'name': f'{label.value} · {t.meta["period"]}' if t.meta.get('period') else label.value}
                                if t.type == 'scatter': patch['line.color'] = color.value
                                if t.type == 'bar': patch['marker.line.color'] = color.value
                                project.edit_traces(name, [patch, [i]])
                        await owner.show_graph()
                    ui.button('apply', on_click=apply_group).props('flat dense').mark(f'apply-color-{group}')
        for index, annotation in enumerate(layout.annotations or []):
            with ui.row().classes('w-full items-end'):
                text = ui.input('annotation', value=annotation.text).classes('grow')
                x = ui.number('annotation · x', value=annotation.x) if isinstance(annotation.x, (int, float)) else ui.input('annotation · x', value=annotation.x)
                y = ui.number('annotation · y', value=annotation.y) if isinstance(annotation.y, (int, float)) else ui.input('annotation · y', value=annotation.y)
                async def apply_annotation(index=index, text=text, x=x, y=y):
                    project.edit_layout(name, {f'annotations[{index}].text': text.value, f'annotations[{index}].x': x.value, f'annotations[{index}].y': y.value})
                    await owner.show_graph()
                ui.button('apply annotation', on_click=apply_annotation).props('flat dense')
        ui.label('resize').classes('font-medium')
        with ui.row().classes('w-full items-end'):
            ratio = ui.input('aspect ratio · width:height', value=f'{figure.layout.width}:{figure.layout.height}' if figure.layout.width else '4:3').classes('w-60').props('outlined dense').mark('aspect-ratio')
            def apply_ratio():
                try:
                    a, b = map(float, ratio.value.split(':'))
                    if not all(math.isfinite(v) and v > 0 for v in (a, b)): raise ValueError
                    size = project.styles.get(name, {}).get('size', {})
                    width = size.get('width', figure.layout.width or 900)
                    height = round(width * b / a)
                    if not project.resize_graph(name, {'width': width, 'height': height}): raise ValueError
                except (ValueError, AttributeError, ZeroDivisionError, OverflowError):
                    ui.notify('Use a positive width:height ratio with a resulting height of 300–4000 px.', type='negative')
                    return
                window.style(f'width:{width + 26}px;height:{height + 26}px')
            ui.button('apply ratio', on_click=apply_ratio).props('flat').mark('apply-ratio')
        async def reset():
            size = project.styles.get(name, {}).get('size')
            project.styles.pop(name, None)
            if size: project.styles[name] = {'size': size}
            await owner.show_graph()
        ui.button('reset graph edits', on_click=reset).props('flat dense')
