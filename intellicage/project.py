from __future__ import annotations

import copy
import io
import json
import math
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import plotly.graph_objects as go
import yaml

from . import plots as P
from .metadata import folder_labels, resolve_labels
from .catalogue import analysis_name
from .pipeline import (DEFAULT_SETTINGS, analyses_for, build_tables, load_datasets,
                       run_metadata, validate_settings)
from .selection import select_data

MAX_UPLOAD = 1024 * 1024 * 1024
MAX_FILES = 10000


def safe_name(name: str) -> str:
    path = PurePosixPath(name.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or not path.parts or ':' in str(path):
        raise ValueError('Unsafe file path')
    return str(path)


def unpack(data: bytes, destination: Path) -> list[Path]:
    if len(data) > MAX_UPLOAD:
        raise ValueError('Upload exceeds 1 GB')
    destination = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_UPLOAD:
            raise ValueError('Archive is too large')
        seen = set()
        for member in members:
            name = safe_name(member.filename)
            if name in seen or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Archive contains duplicate paths or symbolic links')
            seen.add(name)
        paths = []
        for member in members:
            if member.is_dir():
                continue
            path = destination / safe_name(member.filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, path.open('wb') as target:
                remaining = member.file_size
                while chunk := source.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError('Archive has an invalid file size')
                    target.write(chunk)
            paths.append(path)
        return paths


def discover(root: Path) -> list[dict]:
    folders = sorted({p.parent for p in root.rglob('Visit.txt')})
    if not folders:
        raise ValueError('No IntelliCage Visit.txt files found')
    result = []
    for folder in folders:
        missing = [name for name in ['Animal.txt', 'Nosepoke.txt', 'Sessions.txt'] if not (folder / name).is_file()]
        if missing:
            raise ValueError(f'{folder.name}: missing {", ".join(missing)}')
        result.append({'path': str(folder), **folder_labels(folder)})
    return result


def trace_key(trace) -> str:
    return json.dumps(trace.meta or {'name': trace.name}, sort_keys=True)


def layout_patch(event: dict) -> dict:
    # keep edits, never data arrays
    allowed = {'title', 'xaxis', 'yaxis', 'legend', 'annotations', 'shapes', 'font', 'margin',
               'width', 'height', 'showlegend', 'dragmode', 'paper_bgcolor', 'plot_bgcolor'}
    return {key: value for key, value in event.items()
            if re.split(r'[.\[]', key)[0] in allowed and key not in {'width', 'height'}}


def merge_layout(figure: go.Figure, patches: dict):
    # plotly accepts dotted keys, array edits need their parent first
    for key, value in patches.items():
        match = re.match(r'^(annotations|shapes)\[(\d+)\]\.(.+)$', key)
        if match:
            parent, index, child = match.groups()
            items = list(getattr(figure.layout, parent) or [])
            if int(index) < len(items):
                items[int(index)].update({child: value})
                figure.update_layout({parent: items})
        elif match := re.match(r'^(xaxis|yaxis)\.range\[([01])\]$', key):
            axis, index = match.groups()
            limits = list(getattr(figure.layout, axis).range or [None, None])
            limits[int(index)] = value
            figure.update_layout({axis: {'range': limits}})
        else:
            figure.update_layout({key: value})


class Project:
    def __init__(self, cfg: dict):
        self.cfg = copy.deepcopy(cfg)
        self.cfg['settings'] = {**DEFAULT_SETTINGS, **self.cfg.get('settings', {})}
        self.cfg.setdefault('analyses', 'all')
        self.selection = copy.deepcopy(self.cfg.get('selection') or {})
        self.styles = copy.deepcopy(self.cfg.pop('styles', {}))
        self.cache = {}
        self.cfg['datasets'] = [resolve_labels(ds) for ds in self.cfg['datasets']]
        self.datasets = load_datasets(self.cfg['datasets'])
        self.rebuild()

    @property
    def groups(self):
        return self.cfg.get('groups') or list(dict.fromkeys(d.group for d in self.datasets))

    @property
    def phases(self):
        return list(dict.fromkeys(d.phase for d in self.datasets))

    @property
    def starts(self):
        return {(d.group, d.lot, d.phase): d.start for d in self.datasets}

    def rebuild(self, settings=None):
        candidate = {**self.cfg['settings'], **(settings or {})}
        validate_settings(candidate)
        tables = build_tables(self.datasets, candidate)
        if 'mouse_summary' not in tables:
            raise ValueError('No animals remain in these exports')
        self.cfg['settings'] = candidate
        self.tables = tables
        self.analyses = analyses_for(self.cfg, tables)
        self.cache = {}
        self.selection.setdefault('groups', self.groups)
        self.selection.setdefault('phases', self.phases[:1])

    def set_selection(self, selection):
        # validate before changing the active project
        for table in self.tables.values():
            select_data(table, {}, self.groups, selection)
        self.selection = copy.deepcopy(selection)
        self.cache = {}

    def result(self, name):
        cache, selection = self.cache, self.selection
        if name not in cache:
            analysis = next(a for a in self.analyses if analysis_name(a) == name)
            from .catalogue import analysis_section
            from .inference import compute_results
            scope = self.cfg['settings']['family_scope']
            family = [a for a in self.analyses if scope == 'all' or
                      (scope == 'section' and analysis_section(a) == analysis_section(analysis)) or a is analysis]
            cache.update(compute_results(family, self.tables, self.groups,
                P.colors_for(self.groups, self.cfg.get('colors')), self.cfg['settings'], self.starts, selection))
        original = cache[name]
        result = {**original, 'figure': go.Figure(original['figure'])}
        style = self.styles.get(name, {})
        merge_layout(result['figure'], style.get('layout', {}))
        if size := style.get('size'):
            result['figure'].update_layout(**size, autosize=False)
        for trace in result['figure'].data:
            if patch := style.get('traces', {}).get(trace_key(trace)):
                trace.update(patch)
        return result

    def resize_graph(self, name, size):
        values = [size.get(key) for key in ('width', 'height')]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) and 300 <= v <= 4000 for v in values):
            return False
        self.styles.setdefault(name, {})['size'] = dict(zip(('width', 'height'), map(round, values)))
        return True

    def edit_layout(self, name, event):
        patches = layout_patch(event)
        candidate = dict(self.styles.get(name, {}).get('layout', {}))
        for axis in ('xaxis', 'yaxis'):
            if patches.get(f'{axis}.autorange') is True:
                candidate = {k: v for k, v in candidate.items() if not k.startswith(f'{axis}.range')}
            elif any(k.startswith(f'{axis}.range') for k in patches):
                patches[f'{axis}.autorange'] = False
        candidate.update(patches)
        figure = go.Figure(self.cache[name]['figure']) if name in self.cache else self.result(name)['figure']
        merge_layout(figure, candidate)
        self.styles.setdefault(name, {})['layout'] = candidate

    def edit_traces(self, name, event):
        if isinstance(event, dict) and '0' in event and '1' in event:
            event = [event['0'], event['1']]
        if not isinstance(event, (list, tuple)) or len(event) != 2:
            return
        edits, indices = event
        figure = self.result(name)['figure']
        indices = indices if isinstance(indices, list) else [indices]
        allowed = {'name', 'visible', 'marker.color', 'marker.line.color', 'marker.size', 'marker.symbol', 'line.color', 'line.width', 'line.dash', 'opacity'}
        patches = {}
        for pos, index in enumerate(indices):
            trace = figure.data[index]
            patch = {key: (value[pos % len(value)] if isinstance(value, list) and value else value)
                     for key, value in edits.items() if key in allowed}
            trace.update(patch)
            patches[trace_key(trace)] = patch
        target = self.styles.setdefault(name, {}).setdefault('traces', {})
        for key, patch in patches.items():
            target[key] = {**target.get(key, {}), **patch}

    def configuration(self):
        return {**copy.deepcopy(self.cfg), 'selection': copy.deepcopy(self.selection), 'styles': copy.deepcopy(self.styles)}

    def save(self) -> bytes:
        buffer = io.BytesIO()
        cfg = self.configuration()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for i, ds in enumerate(self.datasets):
                prefix = f'inputs/{i}'
                cfg['datasets'][i]['path'] = prefix
                for path in sorted(ds.path.rglob('*.txt')):
                    archive.write(path, f'{prefix}/{path.relative_to(ds.path).as_posix()}')
            manifest = {'format': 'intellicage-project', 'version': 1, 'config': cfg,
                        'provenance': run_metadata(self.datasets, self.configuration())}
            archive.writestr('project.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        return buffer.getvalue()

    @classmethod
    def open(cls, data: bytes, destination: Path):
        unpack(data, destination)
        manifest = json.loads((destination / 'project.json').read_text())
        if manifest.get('format') != 'intellicage-project' or manifest.get('version') != 1:
            raise ValueError('Unsupported project format')
        cfg = manifest['config']
        import hashlib
        for dataset, provenance in zip(cfg['datasets'], manifest['provenance']['datasets'], strict=True):
            path = destination / safe_name(dataset['path'])
            for name, expected in provenance['sha256'].items():
                source = path / safe_name(name)
                if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    raise ValueError('Project input checksum mismatch')
            dataset.setdefault('source_path', provenance['path'])
            dataset['path'] = str(path)
        return cls(cfg)

    def export(self) -> bytes:
        from .pipeline import _report
        buffer = io.BytesIO()
        with tempfile.TemporaryDirectory(prefix='intellicage-export-') as temporary:
            root = Path(temporary)
            for folder in ['tables', 'chart_data', 'stats', 'figures/html', 'figures/json']:
                (root / folder).mkdir(parents=True)
            selected_tables = {name: select_data(table, {}, self.groups, self.selection)
                               for name, table in self.tables.items()}
            for name, table in selected_tables.items():
                table.write_csv(root / 'tables' / f'{name}.csv')
            entries = []
            for analysis in self.analyses:
                name = analysis_name(analysis)
                result = self.result(name)
                P.save(result['figure'], root / 'figures/html' / f'{name}.html')
                result['figure'].write_json(root / 'figures/json' / f'{name}.json')
                result['data'].write_csv(root / 'chart_data' / f'{name}.csv')
                from .prism import analysis_csv
                analysis_csv(analysis, self.tables[analysis['table']], self.selection.get('groups', self.groups),
                             self.selection, result['data']).write(root / 'prism_tables', name)
                for kind, table in result['stats'].items():
                    table.write_csv(root / 'stats' / f'{name}__{kind}.csv')
                entries.append({'name': name, 'title': analysis['title'], 'notes': result['notes'], 'tests': result['tests']})
            _report(root, self.cfg.get('run_name', 'IntelliCage analysis'), entries, self.datasets, selected_tables)
            (root / 'config_used.yaml').write_text(yaml.safe_dump(self.configuration(), allow_unicode=True, sort_keys=False))
            (root / 'run_info.json').write_text(json.dumps(run_metadata(self.datasets, self.configuration()), ensure_ascii=False, indent=2))
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    archive_name = path.relative_to(root).as_posix()
                    with zipfile.ZipFile(buffer, 'a', zipfile.ZIP_DEFLATED) as archive:
                        archive.write(path, archive_name)
        return buffer.getvalue()
