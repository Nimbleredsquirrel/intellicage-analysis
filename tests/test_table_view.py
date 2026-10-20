import asyncio
import io
import math
from datetime import date, datetime
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import polars as pl
import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation
from polars.testing import assert_frame_equal

from intellicage.table_view import TablePreview, column_info, excel_bytes, table_bytes

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


@pytest.mark.parametrize('kind, separator', [('csv', ','), ('txt', '\t')])
def test_text_download_contains_all_rows_and_preserves_values(kind, separator):
    table = pl.DataFrame({'id': range(125), 'text': ['Contrôle\t"one"\nline'] * 124 + [None]})
    restored = pl.read_csv(io.BytesIO(table_bytes(table, kind)), separator=separator)
    assert_frame_equal(restored, table)


def test_excel_is_a_complete_typed_workbook():
    table = pl.DataFrame({'mouse': [1, 2], 'name': ['=SUM(A1)', 'KO & contrôle <test>'], 'paired': [True, False],
        'value': [1.25, None], 'date': [date(2026, 10, 1), None],
        'start': [datetime(2026, 10, 1, 22, 30), None], 'tag': [9920000037859441, 2]})
    with ZipFile(io.BytesIO(table_bytes(table, 'xlsx'))) as archive:
        for name in archive.namelist(): ET.fromstring(archive.read(name))
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        rows = sheet.findall('s:sheetData/s:row', NS)
        assert len(rows) == 3
        assert [c.find('s:is/s:t', NS).text for c in rows[0]] == table.columns
        cells = {c.attrib['r']: c for c in rows[1]}
        assert cells['A2'].find('s:v', NS).text == '1'
        assert cells['B2'].attrib['t'] == 'inlineStr'
        assert cells['B2'].find('s:is/s:t', NS).text == '=SUM(A1)'
        assert not sheet.findall('.//s:f', NS)
        assert cells['C2'].attrib['t'] == 'b'
        assert cells['C2'].find('s:v', NS).text == '1'
        assert float(cells['D2'].find('s:v', NS).text) == 1.25
        assert cells['E2'].attrib['s'] == '1'
        assert float(cells['E2'].find('s:v', NS).text) == (date(2026, 10, 1) - date(1899, 12, 30)).days
        assert cells['F2'].attrib['s'] == '2'
        assert cells['G2'].find('s:is/s:t', NS).text == '9920000037859441'
        assert rows[2][1].find('s:is/s:t', NS).text == 'KO & contrôle <test>'
        assert rows[2][3].find('s:v', NS) is None
        assert sheet.find('s:sheetViews/s:sheetView/s:pane', NS).attrib['state'] == 'frozen'
        assert sheet.find('s:autoFilter', NS).attrib['ref'] == 'A1:G3'


def test_excel_wide_columns_empty_tables_and_special_numbers():
    table = pl.DataFrame({f'field_{i}': [i] for i in range(30)})
    with ZipFile(io.BytesIO(excel_bytes(table))) as archive:
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        assert sheet.find('.//s:c[@r="AA2"]/s:v', NS).text == '26'
        assert sheet.find('s:autoFilter', NS).attrib['ref'] == 'A1:AD2'
    special = pl.DataFrame({'x': [math.nan, math.inf, -math.inf], 'text': ['\x01text', '_x0001_', 'a\rb']})
    with ZipFile(io.BytesIO(excel_bytes(special))) as archive:
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        assert sheet.find('.//s:c[@r="A2"]/s:is/s:t', NS).text == 'nan'
        assert sheet.find('.//s:c[@r="B2"]/s:is/s:t', NS).text == '_x0001_text'
        assert sheet.find('.//s:c[@r="B3"]/s:is/s:t', NS).text == '_x005F_x0001_'
        assert sheet.find('.//s:c[@r="B4"]/s:is/s:t', NS).text == 'a_x000D_b'
    for empty in [pl.DataFrame(), pl.DataFrame(schema={'x': pl.Int64})]:
        with ZipFile(io.BytesIO(excel_bytes(empty))) as archive:
            assert len(ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('s:sheetData/s:row', NS)) == 1
        assert column_info(empty).height == empty.width


def test_export_reports_limits_and_unknown_formats():
    with pytest.raises(ValueError, match='Unknown'): table_bytes(pl.DataFrame(), 'pdf')
    with pytest.raises(ValueError, match='text limit'): excel_bytes(pl.DataFrame({'x': ['a' * 32768]}))
    table = pl.DataFrame({'x': pl.repeat(0, 1048576, eager=True)})
    with pytest.raises(ValueError, match='sheet limits'): excel_bytes(table)


@pytest.mark.asyncio
async def test_preview_head_tail_full_pagination_sort_schema_and_downloads():
    previews = []
    table = pl.DataFrame({'mouse': range(125), 'notes': [None] * 5 + ['long text ' * 20] * 120})
    def root(): previews.append(TablePreview(table, 'mice'))
    async with user_simulation(root) as user:
        await user.open('/')
        view = previews[0]
        assert len(view.grid.options['rowData']) == 25
        assert view.grid.options['rowData'][0]['mouse'] == 0
        assert view.grid.options['defaultColDef']['wrapText']
        assert 'autoSizeStrategy' not in view.grid.options
        with user.client: view.mode.set_value('tail')
        assert view.grid.options['rowData'][0]['mouse'] == 100
        with user.client:
            view.mode.set_value('browse')
            view.limit.set_value(10)
            view.pagination.set_value(13)
        assert [r['mouse'] for r in view.grid.options['rowData']] == list(range(120, 125))
        assert view.pagination.visible
        with user.client:
            view.sort.set_value('mouse')
            view.descending.set_value(True)
            view.pagination.set_value(2)
        assert [r['mouse'] for r in view.grid.options['rowData']] == list(range(114, 104, -1))
        state = view.state()
        with user.client: restored = TablePreview(table.head(5), 'mice', state)
        assert restored.pagination.value == 1
        assert restored.descending.value
        with user.client: view.mode.set_value('schema')
        assert not view.pagination.visible
        assert view.grid.options['rowData'] == [
            {'column': 'mouse', 'dtype': 'Int64', 'missing': 0, 'non_null': 125},
            {'column': 'notes', 'dtype': 'String', 'missing': 5, 'non_null': 120}]
        with user.client:
            for kind in ['csv', 'txt', 'xlsx']: await view.download(kind)
        await asyncio.sleep(.1)
        assert len(user.download.http_responses) == 3
        assert pl.read_csv(io.BytesIO(user.download.http_responses[0].content)).height == 125
        assert ZipFile(io.BytesIO(user.download.http_responses[2].content)).testzip() is None
        with user.client: await view.download('bad')
        assert user.notify.contains('Unknown table format')
