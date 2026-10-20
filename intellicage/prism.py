from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
import zipfile

import polars as pl

from .selection import MOUSE_KEYS, select_data

REFERENCE_TITLES = {
    ('mouse_summary', 'visits_n'): 'Nombre tot de visites',
    ('mouse_summary', 'visits_duration_total_s'): 'VIsits duration',
    ('mouse_summary', 'visits_duration_mean_s'): 'Mean visits duration',
    ('mouse_summary', 'nosepokes_n'): 'Nombre de nosepoke',
    ('mouse_summary', 'licks_n'): 'Nombre de licks',
    ('mouse_summary', 'licks_duration_per_visit_s'): 'Mean duration lick',
    ('corner_summary', 'visit_duration_mean_s'): 'Moyenne de temps passé dans un coin',
    ('corner_summary', 'visits_pct'): 'Pourcentage de visites dans chaque coins',
    ('learning_by_day', 'incorrect_pct'): 'Apprentissage associatif',
}


@dataclass
class CsvTable:
    content: str
    metadata: dict

    def files(self, name: str) -> dict[str, str]:
        files = {f'{name}.csv': self.content,
                 f'{name}.json': json.dumps(self.metadata, ensure_ascii=False, indent=2, default=str)}
        if not self.metadata.get('header'):
            readable = io.StringIO(newline='')
            csv.writer(readable, lineterminator='\n').writerow(self.metadata['columns'])
            files[f'{name}__labelled.csv'] = readable.getvalue() + self.content
            mapping = io.StringIO(newline='')
            writer = csv.DictWriter(mapping, ['group', 'lot', 'animal', 'row', 'column'], lineterminator='\n')
            writer.writeheader()
            writer.writerows(self.metadata['subjects'])
            files[f'{name}__subjects.csv'] = mapping.getvalue()
        return files

    def write(self, folder: Path, name: str):
        folder.mkdir(parents=True, exist_ok=True)
        for filename, content in self.files(name).items():
            (folder / filename).write_text(content, encoding='utf8')


def _cell(value):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return ''
    return value


def graph_csv(analysis: dict, data: pl.DataFrame, groups: list[str]) -> CsvTable:
    """make the column / grouped layout used by prism data sheets."""
    value, by = analysis.get('value'), analysis.get('by')
    title = REFERENCE_TITLES.get((analysis['table'], value), analysis.get('title', value))
    meta = {'title': title, 'metric': value, 'factor': by, 'phase': data['phase'].unique().to_list(),
            'missing': 'empty cell; never zero', 'source': 'computed from selected IntelliCage exports'}
    if not value:
        return CsvTable(data.write_csv(), {**meta, 'layout': 'events', 'header': True})
    if data['phase'].n_unique() > 1:
        raise ValueError('Select one phase before exporting a Prism table')
    factors = [by] if by else []
    if analysis.get('plot') == 'period_timecourse':
        factors.append('period')
    keys = [*MOUSE_KEYS, *factors]
    if data.group_by(keys).len().filter(pl.col('len') > 1).height:
        raise ValueError('More than one value per mouse and table row')
    subjects = {g: data.filter(group=g).select('lot', 'animal').unique().sort('lot', 'animal').rows() for g in groups}
    values = {tuple(row[k] for k in keys): row[value] for row in data.iter_rows(named=True)}
    rows, identities = [], []
    if not factors:
        for i in range(max((len(s) for s in subjects.values()), default=0)):
            row = []
            for column, group in enumerate(groups):
                if i < len(subjects[group]):
                    lot, animal = subjects[group][i]
                    row.append(_cell(values[(group, lot, animal)]))
                    identities.append({'group': group, 'lot': lot, 'animal': animal, 'row': i + 1, 'column': column + 1})
                else:
                    row.append('')
            rows.append(row)
        meta.update(layout='column', columns=groups, subjects=identities)
    else:
        levels = data.select(factors).unique().sort(factors).rows()
        width = max((len(s) for s in subjects.values()), default=0)
        labels = []
        columns = ['row title']
        for gi, group in enumerate(groups):
            for i in range(width):
                columns.append(group)
                if i < len(subjects[group]):
                    lot, animal = subjects[group][i]
                    identities.append({'group': group, 'lot': lot, 'animal': animal, 'column': 2 + gi * width + i})
        for level in levels:
            label = ' · '.join(f'Coin {x}' if factor == 'corner' else f'J{x}' if factor == 'day' else str(x)
                               for factor, x in zip(factors, level))
            row = [label]
            for group in groups:
                for i in range(width):
                    if i < len(subjects[group]):
                        lot, animal = subjects[group][i]
                        row.append(_cell(values.get((group, lot, animal, *level))))
                    else:
                        row.append('')
            rows.append(row)
            labels.append(dict(zip(factors, level)))
        meta.update(layout='grouped', columns=columns, subjects=identities, replicates=width, row_levels=labels)
    output = io.StringIO(newline='')
    csv.writer(output, lineterminator='\n').writerows(rows)
    meta.update(header=False, rows=len(rows), column_count=len(meta['columns']),
                identity_note='positions are 1-based in the headerless CSV; labelled CSV adds a header row; different groups are not paired by row number')
    if analysis.get('plot') == 'latency':
        meta['censoring'] = 'empty latency cells can be right-censored; see the matching chart_data CSV for window_s and censor flags'
    return CsvTable(output.getvalue(), meta)


def analysis_csv(analysis, source, groups, selection=None, plotted=None):
    # keep undefined means as empty cells, with the mouse still in its slot
    filters = {k: v for k, v in analysis.items() if k != 'value'}
    data = select_data(source, filters, groups, selection)
    if analysis.get('common_levels') and plotted is not None:
        data = data.filter(pl.col(analysis['by']).is_in(plotted[analysis['by']].unique().to_list()))
    result = graph_csv(analysis, data, groups)
    if 'recording_gap_s' in data.columns:
        result.metadata['recording'] = {'max_gap_s': data['recording_gap_s'].max(),
                                        'exposure': 'actual hours and recording_gap_s are in the matching chart_data CSV'}
    return result


@dataclass
class ReferenceTable:
    title: str
    layout: str
    groups: list[str]
    replicates: int
    row_titles: bool
    content: str
    table_id: str
    x_columns: int = 0

    def group_values(self, group: str) -> list[list[str]]:
        index = self.groups.index(group)
        start = int(self.row_titles) + self.x_columns + index * self.replicates
        rows = list(csv.reader(io.StringIO(self.content)))
        result = [row[start:start + self.replicates] for row in rows]
        while result and not any(result[-1]):
            result.pop()
        return result


def read_reference(path: str | Path) -> list[ReferenceTable]:
    """read original data sheets without extracting or changing the project."""
    tables = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for name in sorted(names):
            if not name.startswith('data/sheets/') or not name.endswith('/sheet.json'):
                continue
            sheet = json.loads(archive.read(name))
            table = sheet['table']
            title = sheet['title']
            if table['format'] == 'view' or ':Estimation Plot' in title:
                continue
            groups = []
            for uid in table.get('dataSets', []):
                dataset = json.loads(archive.read(f'data/sets/{uid}.json'))
                text = dataset.get('title', '')
                groups.append((text.get('string', '') if isinstance(text, dict) else text).strip())
            csv_path = f'data/tables/{table["uid"]}/data.csv'
            if csv_path not in names:
                raise ValueError(f'{title}: no embedded CSV data')
            tables.append(ReferenceTable(title, table['format'], groups, table.get('replicatesCount', 1),
                                         bool(table.get('rowTitlesDataSet')), archive.read(csv_path).decode('utf-8-sig'), table['uid'], int(bool(table.get('xDataSet')))))
    return tables


def compare_reference(tables: dict[str, pl.DataFrame], reference: ReferenceTable) -> pl.DataFrame:
    """compare every filled cell; grouped mouse identities are inferred, not stored in prism."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    matches = [key for key, title in REFERENCE_TITLES.items() if title == reference.title]
    if not matches:
        return pl.DataFrame([{'title': reference.title, 'status': 'unmapped reference',
                              'note': 'No verified metric mapping; do not assume this is the same observation period.'}])
    source, metric = matches[0]
    factor = 'corner' if source == 'corner_summary' else 'day' if source == 'learning_by_day' else None
    rows = []
    for group in reference.groups:
        sub = tables[source].filter(group=group)
        if sub['phase'].n_unique() > 1:
            raise ValueError('Select one phase before comparing a Prism reference')
        expected = reference.group_values(group)
        if factor:
            profiles = [(i, [row[i] for row in expected]) for i in range(reference.replicates)
                        if any(row[i] for row in expected)]
            subjects = sub.select('lot', 'animal').unique().sort('lot', 'animal').rows()
            levels = sorted(sub[factor].unique())
            if len(expected) != len(levels):
                raise ValueError(f'{reference.title}: different repeated levels')
            observed = [[sub.filter(lot=lot, animal=animal, **{factor: level})[metric][0]
                         for level in levels] for lot, animal in subjects]
            ref_values = [[float(x) if x else np.nan for x in values] for _, values in profiles]
            cost = np.array([[np.nanmax(np.abs(np.array(a, dtype=float) - b)) for b in ref_values] for a in observed])
            ai, bi = linear_sum_assignment(np.nan_to_num(cost, nan=1e100))
            pairs = [(subjects[i], profiles[j][0], observed[i], profiles[j][1]) for i, j in zip(ai, bi)]
            if len(subjects) != len(profiles):
                raise ValueError(f'{reference.title}: different mouse counts in {group}')
        else:
            expected_values = [row[0] for row in expected if row and row[0]]
            subjects = sub.sort('lot', 'animal').select('lot', 'animal').rows()
            observed = sub.sort('lot', 'animal')[metric].to_list()
            if len(subjects) != len(expected_values):
                raise ValueError(f'{reference.title}: different mouse counts in {group}')
            pairs = [(subject, i, [actual], [expected_values[i]])
                     for i, (subject, actual) in enumerate(zip(subjects, observed))]
        for (lot, animal), slot, actual, values in pairs:
            for i, (calculated, text) in enumerate(zip(actual, values, strict=True)):
                if not text:
                    continue
                target = float(text)
                # reference decimal places define its recorded precision
                from decimal import Decimal
                tolerance = (.05 if source == 'learning_by_day' else
                             0. if metric.endswith('_n') else .5 * 10 ** Decimal(text).as_tuple().exponent) + 1e-10
                difference = calculated - target if calculated is not None else None
                rows.append({'title': reference.title, 'metric': metric, 'group': group, 'lot': lot, 'animal': animal,
                             'level': str(levels[i]) if factor else '', 'reference_slot': slot + 1,
                             'reference': target, 'computed': calculated, 'difference': difference,
                             'tolerance': tolerance,
                             'status': 'match' if difference is not None and abs(difference) <= tolerance else 'different',
                             'identity': 'inferred from complete profile' if factor else 'sorted mouse order; check source identity'})
    return pl.DataFrame(rows)
