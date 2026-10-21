from __future__ import annotations

import io
import json
import math
import re
from datetime import date, datetime
from itertools import chain
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import polars as pl
from nicegui import run, ui


def column_info(table: pl.DataFrame) -> pl.DataFrame:
    missing = table.null_count().row(0, named=True)
    return pl.DataFrame({'column': table.columns, 'dtype': [str(t) for t in table.dtypes],
                         'missing': [missing[c] for c in table.columns],
                         'non_null': [table.height - missing[c] for c in table.columns]})


def _letter(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell(value, address, *, header=False):
    if value is None: return f'<c r="{address}"/>'
    if isinstance(value, bool): return f'<c r="{address}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (datetime, date)):
        stamp = value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())
        serial = (stamp - datetime(1899, 12, 30)).total_seconds() / 86400
        return f'<c r="{address}" s="{2 if isinstance(value, datetime) else 1}"><v>{serial}</v></c>'
    if isinstance(value, (int, float)) and math.isfinite(value) and not (isinstance(value, int) and abs(value) >= 10**15):
        return f'<c r="{address}"><v>{value}</v></c>'
    text = str(value)
    if len(text.encode('utf-16-le')) // 2 > 32767: raise ValueError('A cell exceeds the Excel text limit; download CSV or TXT instead')
    text = re.sub(r'_x[0-9a-fA-F]{4}_', lambda m: '_x005F_' + m[0][1:], text)
    text = re.sub(r'[\x00-\x08\x0b-\x1f]', lambda m: f'_x{ord(m[0]):04X}_', text)
    style = ' s="3"' if header else ''
    return f'<c r="{address}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def excel_bytes(table: pl.DataFrame) -> bytes:
    # a small spreadsheetml workbook, no office installation needed
    if table.height >= 1048576 or table.width > 16384:
        raise ValueError('This table exceeds the Excel sheet limits; download CSV or TXT instead')
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" xmlns:r="{rel}"><bookViews><workbookView/></bookViews><sheets>'
            '<sheet name="data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{rel}/styles" Target="styles.xml"/></Relationships>')
        archive.writestr('xl/styles.xml', f'<styleSheet xmlns="{main}"><numFmts count="2">'
            '<numFmt numFmtId="164" formatCode="yyyy-mm-dd"/><numFmt numFmtId="165" formatCode="yyyy-mm-dd hh:mm:ss.000"/></numFmts>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment wrapText="1"/></xf></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        with archive.open('xl/worksheets/sheet1.xml', 'w') as sheet:
            sheet.write((f'<worksheet xmlns="{main}"><sheetViews><sheetView workbookViewId="0">'
                         '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                         '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/>').encode())
            if table.width: sheet.write(b'<cols>')
            for i, name in enumerate(table.columns, 1):
                width = min(64, max(18, len(name) + 2))
                sheet.write(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'.encode())
            if table.width: sheet.write(b'</cols>')
            sheet.write(b'<sheetData>')
            for row, values in enumerate(chain([table.columns], table.iter_rows()), 1):
                cells = ''.join(_cell(v, f'{_letter(i)}{row}', header=row == 1) for i, v in enumerate(values, 1))
                sheet.write(f'<row r="{row}">{cells}</row>'.encode('utf-8'))
            sheet.write(b'</sheetData>')
            if table.width: sheet.write(f'<autoFilter ref="A1:{_letter(table.width)}{table.height + 1}"/>'.encode())
            sheet.write(b'</worksheet>')
    return buffer.getvalue()


def table_bytes(table, kind):
    if kind == 'xlsx': return excel_bytes(table)
    if kind not in ('csv', 'txt'): raise ValueError('Unknown table format')
    return table.write_csv(separator='\t' if kind == 'txt' else ',').encode('utf-8')


def grid(table):
    columns = [{'field': c, 'tooltipField': c} for c in table.columns]
    rows = json.loads(table.write_json())
    for row, original in zip(rows, table.iter_rows(named=True)):
        for key, value in original.items():
            if isinstance(value, float) and not math.isfinite(value): row[key] = str(value)
    ui.add_css("""
.table-preview-grid.ag-layout-auto-height .ag-center-cols-viewport,
.table-preview-grid.ag-layout-auto-height .ag-center-cols-container,
.table-preview-grid .ag-layout-auto-height .ag-center-cols-viewport,
.table-preview-grid .ag-layout-auto-height .ag-center-cols-container {min-height:0!important}
""")
    with ui.element('div').classes('w-full min-w-0').style(
            'max-height:min(65vh, 620px);overflow:auto').mark('preview-viewport'):
        return ui.aggrid({'columnDefs': columns, 'domLayout': 'autoHeight',
            'defaultColDef': {'width': 210, 'minWidth': 140, 'resizable': True, 'wrapHeaderText': True, 'sortable': False, 'filter': False,
                             'autoHeaderHeight': True, 'wrapText': True, 'autoHeight': True},
            'rowData': rows, 'tooltipShowDelay': 250,
            'enableCellTextSelection': True, 'ensureDomOrder': True}, auto_size_columns=False).classes(
                'w-full min-w-0 table-preview-grid').style('height:auto;min-height:0;flex:none')


class TablePreview:
    def __init__(self, table, name='table', state=None):
        state = state or {}
        self.table, self.name, self.updating = table, name, False
        ui.label(f'{table.height:,} rows × {table.width} columns · {table.estimated_size("mb"):.2f} MB').classes('muted')
        with ui.row().classes('w-full items-center gap-3'):
            self.mode = ui.toggle({'head': 'first rows', 'tail': 'last rows', 'browse': 'all rows', 'schema': 'column info'}, value=state.get('mode', 'head'))
            self.limit = ui.select([10, 25, 50, 100], value=state.get('limit', 25), label='rows per page').props('behavior=dialog').classes('w-36')
            for kind, label in [('csv', 'CSV'), ('txt', 'TXT'), ('xlsx', 'Excel')]:
                ui.button(label, on_click=lambda kind=kind: self.download(kind)).props('flat dense')
        with ui.row().classes('w-full items-center') as self.sort_controls:
            self.sort = ui.select({'': 'original order', **{c: c for c in table.columns}}, value=state.get('sort', '') if state.get('sort', '') in table.columns else '', label='sort all rows by').props('behavior=dialog').classes('w-64')
            self.descending = ui.checkbox('descending', value=state.get('descending', False))
        pages = max(1, math.ceil(table.height / self.limit.value))
        self.pagination = ui.pagination(1, pages, value=min(pages, state.get('page', 1)), direction_links=True).props('boundary-links max-pages=7')
        self.caption = ui.label().classes('muted')
        self.content = ui.column().classes('w-full min-w-0')
        for control in (self.mode, self.limit, self.sort, self.descending): control.on_value_change(self.reset)
        self.pagination.on_value_change(self.draw)
        self.draw()

    def state(self):
        return {'mode': self.mode.value, 'limit': self.limit.value, 'sort': self.sort.value,
                'descending': self.descending.value, 'page': self.pagination.value}

    def reset(self):
        self.updating = True
        self.pagination.set_value(1)
        self.updating = False
        self.draw()

    def draw(self):
        if self.updating: return
        mode, limit = self.mode.value, int(self.limit.value)
        table = self.table.sort(self.sort.value, descending=self.descending.value, nulls_last=True) if self.sort.value and mode != 'schema' else self.table
        self.pagination.max = max(1, math.ceil(table.height / limit))
        self.pagination.set_visibility(mode == 'browse')
        self.sort_controls.set_visibility(mode != 'schema')
        self.limit.set_visibility(mode != 'schema')
        left = max(0, table.height - limit) if mode == 'tail' else ((self.pagination.value or 1) - 1) * limit if mode == 'browse' else 0
        preview = column_info(table) if mode == 'schema' else table.slice(left, limit)
        self.caption.set_text('column names, Polars types and missing values · full table' if mode == 'schema' else
                              f'rows {left + 1 if table.height else 0}–{min(left + limit, table.height)} of {table.height:,} · scroll sideways for all columns')
        self.content.clear()
        with self.content: self.grid = grid(preview)

    async def download(self, kind):
        try:
            data = await run.io_bound(table_bytes, self.table, kind)
            ui.download.content(data, f'{self.name}.{kind}')
        except (ValueError, OSError) as exc:
            ui.notify(str(exc), type='negative')


class TableChoice:
    def __init__(self, on_change):
        self.options, self.value, self.on_change = {}, None, on_change
        with ui.column().classes('gap-1 w-96 max-w-full shrink-0'):
            ui.label('table').classes('muted')
            self.button = ui.button('choose table', icon='table_chart', on_click=self.open).props('outline no-caps').classes('w-full').mark('table-choice').disable()
        with ui.dialog() as self.dialog:
            with ui.card().classes('w-full max-w-xl').style('width:560px;max-height:80vh'):
                with ui.row().classes('w-full items-center'):
                    ui.label('choose table').classes('text-lg font-bold grow')
                    ui.button(icon='close', on_click=self.dialog.close).props('flat round dense')
                self.search = ui.input('search tables', on_change=self.refresh).classes('w-full').props('clearable').mark('table-search')
                self.choices = ui.column().classes('w-full gap-1').style('max-height:50vh;overflow:auto')

    def set_options(self, options, *, value=None):
        self.options = dict(options)
        self.button.set_enabled(bool(options))
        if not options: self.dialog.close()
        self.set_value(value if value in options else None)
        self.refresh()

    def set_value(self, value):
        if value is not None and value not in self.options: raise ValueError('Unknown table')
        previous, self.value = self.value, value
        self.button.set_text(self.options.get(value, 'choose table'))
        if value != previous: self.on_change()

    def open(self):
        self.search.set_value('')
        self.refresh()
        self.dialog.open()

    def refresh(self):
        self.choices.clear()
        query = (self.search.value or '').casefold().strip()
        with self.choices:
            matches = [(key, label) for key, label in self.options.items() if query in label.casefold() or query in key.casefold()]
            if not matches: ui.label('No matching tables.').classes('muted')
            for key, label in matches:
                ui.button(label, icon='check' if key == self.value else 'table_chart',
                          on_click=lambda key=key: self.choose(key)).props('flat no-caps align=left').classes('w-full').mark(f'table-option-{key}')

    def choose(self, value):
        self.dialog.close()
        self.set_value(value)
