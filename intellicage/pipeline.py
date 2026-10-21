from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime
from html import escape
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

import numpy as np
import polars as pl
import yaml

from . import metrics as M, plots as P, stats as S
from .catalogue import analysis_name, default_analyses
from .io import Dataset, load_dataset
from .metadata import resolve_labels, validate_labels
from .selection import present_groups, select_data
from .timebins import period_windows

DEFAULT_SETTINGS = {'light_on': 8, 'light_off': 20, 'latency_window_min': None, 'min_day_hours': 12,
                    'test': 'welch', 'repeated_test': 'mixed_anova', 'posthoc': 'welch', 'padjust': 'sidak',
                    'bar_style': 'bar', 'error': 'sem', 'include_partial_periods': False,
                    'gate_complete_only': False, 'export_images': False, 'significance': 'stars', 'show_ns': True,
                    'show_test_summary': True, 'show_n': False, 'family_scope': 'graph', 'period_style': 'timecourse'}
TABLES = ['mouse_summary', 'corner_summary', 'learning_by_day', 'activity_hourly', 'activity_elapsed',
          'activity_circadian', 'activity_periods', 'activity_light_dark', 'latency', 'gate_sessions',
          'gate_summary', 'gate_hourly']
BY_DEFAULTS = {'corner': {'plot': 'grouped', 'xlabel': 'Corner'},
               'day': {'plot': 'timecourse', 'xlabel': 'Day from phase start'},
               'period': {'plot': 'grouped', 'xlabel': 'Observation window'},
               'hour': {'plot': 'timecourse', 'xlabel': 'Hour'},
               'clock_hour': {'plot': 'timecourse', 'xlabel': 'Clock hour'}}


def _slug(text):
    return analysis_name({'name': text})


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding='utf8'))
    if not isinstance(cfg, dict) or not cfg.get('datasets'):
        raise ValueError('Analysis needs at least one dataset; add folders in the app and save a project, or supply dataset paths for batch analysis')
    cfg['settings'] = {**DEFAULT_SETTINGS, **(cfg.get('settings') or {})}
    validate_settings(cfg['settings'])
    return cfg


def validate_settings(settings):
    windows = period_windows(settings)
    if settings.get('period_style') == 'paired' and len(windows) != 2:
        raise ValueError('Paired view requires exactly two day/night windows')
    for key, choices in {'error': ['sem', 'sd', 'ci95'], 'bar_style': ['bar', 'scatter'],
                         'significance': ['p', 'stars', 'both', 'none'], 'family_scope': ['graph', 'section', 'all'],
                         'period_style': ['timecourse', 'paired']}.items():
        if settings.get(key, DEFAULT_SETTINGS[key]) not in choices:
            raise ValueError(f'Unknown {key}')
    for key in ('show_n', 'show_ns', 'show_test_summary'):
        if not isinstance(settings.get(key, DEFAULT_SETTINGS[key]), bool):
            raise ValueError(f'{key} must be a boolean')
    window = settings['latency_window_min']
    if window is not None and (isinstance(window, bool) or not np.isfinite(float(window)) or float(window) <= 0):
        raise ValueError('Latency window must be positive minutes or null for the full phase')
    if not 0 < float(settings['min_day_hours']) <= 24:
        raise ValueError('Minimum day length must be between 0 and 24 hours')
    if settings['test'] not in S.TWO_GROUP_TESTS + S.MULTI_GROUP_TESTS:
        raise ValueError('Unknown independent test')
    if settings['repeated_test'] not in S.REPEATED_TESTS:
        raise ValueError('Unknown repeated test')
    if settings['padjust'] not in S.PADJUST:
        raise ValueError('Unknown P adjustment')


def load_datasets(datasets: list[dict]) -> list[Dataset]:
    keys, paths, result = set(), set(), []
    for spec in datasets:
        d = resolve_labels(spec)
        validate_labels(d)
        path = Path(d['path']).expanduser().resolve()
        key = (d['group'], str(d.get('lot', '')), str(d.get('phase', '')))
        if key in keys or path in paths:
            raise ValueError('Each export needs its own path and group/lot/phase combination')
        keys.add(key); paths.add(path)
        result.append(load_dataset(path, group=key[0], lot=key[1], phase=key[2],
                                   learning=d.get('learning'), exclude_animals=d.get('exclude_animals')))
    return result


def build_tables(datasets: list[Dataset], settings: dict) -> dict[str, pl.DataFrame]:
    validate_settings(settings)
    out = {t: [] for t in TABLES}
    for ds in datasets:
        summary = M.mouse_summary(ds)
        hourly = M.activity_hourly(ds, settings['light_on'], settings['light_off'])
        elapsed = M.activity_hourly(ds, settings['light_on'], settings['light_off'], align='phase')
        periods = M.activity_periods(ds, settings)
        sessions = M.gate_sessions(ds)
        selected_sessions = sessions.filter('paired') if sessions is not None and settings['gate_complete_only'] else sessions
        gate_hourly = M.gate_hourly(ds, selected_sessions)
        if gate_hourly is not None:
            gate_hourly = gate_hourly.join(elapsed.select(*M.IDS, 'hour', 'visits_n'), on=[*M.IDS, 'hour'], how='left')
            gate_hourly = gate_hourly.with_columns(visits_per_gate_session=M._ratio(pl.col('visits_n'), pl.col('gate_sessions_n')))
        values = [summary, M.corner_summary(ds), M.learning_by_day(ds, settings['min_day_hours']), hourly, elapsed,
                  M.activity_circadian(hourly), periods, M.activity_light_dark(periods, settings['include_partial_periods']),
                  M.latency(ds, settings['latency_window_min']), sessions,
                  M.gate_summary(ds, selected_sessions, summary), gate_hourly]
        for name, table in zip(TABLES, values):
            if table is not None and table.height:
                out[name].append(table)
    return {name: pl.concat(tables, how='diagonal_relaxed') for name, tables in out.items() if tables}


def analyses_for(cfg, tables):
    if cfg.get('analyses', 'all') == 'all':
        return default_analyses(tables, cfg['settings'])
    analyses = [dict(a) for a in cfg.get('analyses', [])]
    names = [analysis_name(a) for a in analyses]
    if len(names) != len(set(names)):
        raise ValueError('Analysis names must be unique; add an explicit name to filtered variants')
    return analyses


def analyse(a: dict, sub: pl.DataFrame, groups: list[str], colors: dict, settings: dict, *, title: str) -> dict:
    value, by, plot = a.get('value'), a.get('by'), a.get('plot')
    result = {'stats': {}, 'tests': [], 'notes': [], 'data': sub}
    if 'recording_gap_s' in sub.columns and sub.height and sub['recording_gap_s'].max() > 0:
        result['notes'].append(f"Recording interruptions are present (up to {sub['recording_gap_s'].max():g} s per window). Counts use recorded events; actual observation hours and gaps are in the CSV.")
    if a.get('table') == 'latency':
        window = settings['latency_window_min']
        result['notes'].append('First visits are selected from phase start through ' + ('the full phase.' if window is None else f'the first {window:g} minutes.'))
        if sub['followup_interrupted'].any():
            result['notes'].append('Follow-up stops at the first recording gap because an earlier visit during the gap cannot be ruled out.')
        if plot != 'latency':
            result['notes'].append('Duration includes the whole first visit, even if it ends after the chosen window. Missing or incompletely recorded first visits have no duration value.')
    if a.get('common_levels') and len(groups) > 1:
        shared = set.intersection(*(set(sub.filter(group=g)[by]) for g in groups))
        original_levels = sub[by].n_unique()
        sub = sub.filter(pl.col(by).is_in(list(shared)))
        result['data'] = sub
        if len(shared) < original_levels:
            result['notes'].append('Only hours observed in every selected group are compared and plotted.')
    if sub.is_empty():
        raise ValueError('No common observations for the selected groups')
    run_stats = a.get('stats', True)
    if plot == 'gate_raster':
        raise ValueError('Gate raster needs phase start metadata')
    if value:
        desc_keys = ['group'] + ([by] if by else []) + (['period'] if plot == 'period_timecourse' else [])
        finite = sub.filter(pl.col(value).is_finite())
        result['stats']['descriptive'] = finite.group_by(desc_keys).agg(
            n=pl.len(), mean=pl.col(value).mean(), sd=pl.col(value).std(),
            sem=pl.col(value).std() / pl.len().sqrt()).sort(desc_keys)
    pairwise = posthoc = None
    try:
        if plot == 'latency':
            desc, pairwise = S.compare_survival(sub, value, groups, a.get('padjust', settings['padjust']))
            result['stats']['censoring'] = desc
            result['notes'].append('Open triangles are right-censored: no visit was observed within the latency window.')
            if run_stats:
                result['stats']['test'] = pairwise
                result['tests'] = pairwise.to_dicts()
        elif plot == 'period_timecourse':
            if run_stats:
                periods = [w['name'] for w in period_windows(settings)]
                posthoc = S.period_comparisons(sub, value, groups, periods, paired_test=a.get('paired_test', 'paired_t'),
                                              posthoc=a.get('posthoc', settings['posthoc']), padjust=a.get('padjust', settings['padjust']))
                result['stats']['posthoc'] = posthoc
                result['notes'].append('Day/night tests pair mice within each cycle. See family metadata in the statistics CSV for the correction scope.')
        elif not by:
            S.validate_observations(sub)
            if run_stats and len(groups) >= 2:
                desc, pairwise = S.compare_groups(sub, value, groups, a.get('test', settings['test']))
                result['stats']['descriptive'] = desc
                result['stats']['test'] = pairwise
                result['tests'] = pairwise.to_dicts()
                if pairwise.is_empty(): result['notes'].append('At least two mice per group are needed for this test.')
        elif run_stats and len(groups) >= 2:
            omnibus, posthoc = S.repeated_measures(sub, value, by, groups, a.get('test', settings['repeated_test']),
                                                   a.get('posthoc', settings['posthoc']), a.get('padjust', settings['padjust']))
            result['stats'].update(omnibus=omnibus, posthoc=posthoc)
            result['tests'] = omnibus.to_dicts()
            result['notes'].extend(n for n in omnibus['note'].drop_nulls().unique() if n)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        if not by and plot != 'latency':
            raise
        result['notes'].append(f'Statistics unavailable: {exc}')
    if value and plot != 'latency':
        result['stats']['descriptive'] = S.descriptive_intervals(result['stats']['descriptive'])
        result['notes'].append('Error bars: ' + error_label(a.get('error', settings['error'])) + '. Mean confidence intervals are pointwise t intervals, not corrected for multiple comparisons; they require at least two mice.')
    if plot == 'period_timecourse':
        result['stats']['paired_values'] = S.paired_values(sub, value, [w['name'] for w in period_windows(settings)])
        result['stats']['paired_descriptive'] = S.paired_descriptive(result['stats']['paired_values'])
    common = {'title': title, 'ylabel': a.get('ylabel', value), 'ylim': a.get('ylim')}
    error = a.get('error', settings['error'])
    if plot == 'latency':
        result['figure'] = P.latency_plot(sub, value, groups, colors, pairwise=pairwise if run_stats else None, **common)
    elif plot == 'period_timecourse':
        if settings.get('period_style') == 'paired':
            result['figure'] = P.paired_period_plot(result['stats']['paired_values'], groups, colors, error=error,
                                                            show_n=settings.get('show_n', True), **common)
        else:
            result['figure'] = P.period_timecourse(sub, value, groups, [w['name'] for w in period_windows(settings)],
                                                   colors, posthoc=posthoc, error=error, **common)
    elif not by:
        result['figure'] = P.column_plot(sub, value, groups, colors, pairwise=pairwise,
                                         style=a.get('style', settings['bar_style']), error=error, **common)
    else:
        bd = BY_DEFAULTS.get(by, {'plot': 'grouped', 'xlabel': by})
        labels = {x: f'J{x}' if by == 'day' else f'Corner {x}' for x in sub[by].unique()} if by in ('day', 'corner') else None
        if a.get('plot', bd['plot']) == 'timecourse':
            result['figure'] = P.timecourse_plot(sub, value, by, groups, colors, posthoc=posthoc, error=error,
                                                 x_labels=labels, xlabel=a.get('xlabel', bd['xlabel']),
                                                 dark_col='dark' if 'dark' in sub.columns and a.get('shade_dark', True) else None,
                                                 show_points=a.get('show_points', True), **common)
        else:
            result['figure'] = P.grouped_plot(sub, value, by, groups, colors, posthoc=posthoc, error=error,
                                              category_labels=labels, xlabel=a.get('xlabel', bd['xlabel']), **common)
    return result


def error_label(error):
    return {'sem': 'SEM', 'sd': 'SD', 'ci95': '95% CI of the mean'}[error]


def render_analysis(a, table, groups, colors, settings, phase_starts, selection=None, *, decorate=True):
    sub = select_data(table, a, groups, selection)
    present = present_groups(sub, groups)
    if sub.is_empty():
        raise ValueError('No data for this selection')
    if sub['phase'].n_unique() > 1:
        raise ValueError('Select one phase before comparing mice')
    if a.get('plot') == 'gate_raster':
        return {'figure': P.gate_raster(sub, phase_starts, present, colors, title=a.get('title', 'AnimalGate sessions')),
                'data': sub, 'stats': {}, 'tests': [], 'notes': []}
    result = analyse(a, sub, present, colors, settings, title=a.get('title', a['value']))
    from .inference import annotate, sample_sizes
    eligible = select_data(table, {key: val for key, val in a.items() if key != 'value'}, groups, selection)
    if a.get('common_levels'):
        eligible = eligible.filter(pl.col(a['by']).is_in(result['data'][a['by']].unique().implode()))
    result['stats']['sample_sizes'] = sample_sizes(eligible, a)
    result['notes'].append('n counts mice, not events. Missing includes unavailable mouse values within the selected cohort. Latency n includes right-censored mice.')
    if decorate: annotate(a, result, settings)
    return result


def _run_analysis(a, tables, groups, colors, settings, out, phase_starts, selection=None, styles=None, prepared=None):
    if a['table'] not in tables:
        print(f"  skip {analysis_name(a)}: no data")
        return []
    data = select_data(tables[a['table']], a, groups, selection)
    entries = []
    several = data['phase'].n_unique() > 1
    for phase in sorted(data['phase'].unique()):
        selected = {**(selection or {}), 'phases': [phase]}
        if prepared is not None:
            import copy
            res = copy.deepcopy(prepared[phase][analysis_name(a)])
        else:
            from .inference import compute_results
            res = compute_results([a], tables, groups, colors, settings, phase_starts, selected)[analysis_name(a)]
        if style := (styles or {}).get(analysis_name(a)):
            from .project import merge_layout, trace_key
            merge_layout(res['figure'], style.get('layout', {}))
            if size := style.get('size'):
                res['figure'].update_layout(**size, autosize=False)
            for trace in res['figure'].data:
                if patch := style.get('traces', {}).get(trace_key(trace)):
                    trace.update(patch)
        from .graph_layout import fit_figure
        fit_figure(res['figure'], patches=(styles or {}).get(analysis_name(a), {}).get('layout', {}))
        name = analysis_name(a) + (f'_{_slug(phase)}' if several else '')
        P.save(res['figure'], out / 'figures/html' / f'{name}.html',
               out / 'figures/png' / f'{name}.png' if settings['export_images'] else None)
        res['figure'].write_json(out / 'figures/json' / f'{name}.json')
        res['data'].write_csv(out / 'chart_data' / f'{name}.csv')
        from .prism import analysis_csv
        analysis_csv(a, tables[a['table']], groups, selected, res['data']).write(out / 'prism_tables', name)
        for kind, table in res['stats'].items():
            table.write_csv(out / 'stats' / f'{name}__{kind}.csv')
        entries.append({'name': name, 'title': a.get('title', name), 'section': a.get('section', 'Analysis'),
                        'tests': res['tests'], 'notes': res['notes']})
    return entries


def run_metadata(datasets, cfg):
    packages = {}
    for name in ['polars', 'numpy', 'scipy', 'statsmodels', 'pingouin', 'plotly', 'nicegui']:
        try: packages[name] = version(name)
        except PackageNotFoundError: pass
    sources = []
    for ds in datasets:
        files = {str(p.relative_to(ds.path)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(ds.path.rglob('*.txt'))}
        sources.append({'group': ds.group, 'lot': ds.lot, 'phase': ds.phase, 'path': str(ds.path),
                        'start': str(ds.start), 'end': str(ds.end), 'mice': ds.animals['Animal'].to_list(),
                        'learning': ds.learning, 'warnings': ds.warnings, 'sha256': files})
    return {'generated': datetime.now().isoformat(timespec='seconds'),
            'groups': (cfg.get('selection') or {}).get('groups', cfg.get('groups')), 'selection': cfg.get('selection', {}),
            'settings': cfg['settings'], 'packages': packages, 'datasets': sources,
            'code_revision': _code_revision(),
            'code_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(Path(__file__).parent.glob('*.py'))}}


def _code_revision():
    import subprocess
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).parent,
                                       stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _report(out, name, entries, datasets, tables):
    cards = []
    for entry in entries:
        notes = '<br>'.join(escape(note) for note in entry['notes'])
        cards.append(f'<article><h2>{escape(entry["title"])}</h2>'
                     f'<iframe loading="lazy" src="figures/html/{entry["name"]}.html"></iframe>'
                     f'<p>{notes}</p><a href="figures/html/{entry["name"]}.html">open graph</a> · '
                     f'<a href="chart_data/{entry["name"]}.csv">graph data</a> · '
                     f'<a href="prism_tables/{entry["name"]}.csv">Prism CSV</a> · '
                     f'<a href="prism_tables/{entry["name"]}.json">groups &amp; mouse IDs</a></article>')
    links = ' · '.join(f'<a href="tables/{t}.csv">{t}</a>' for t in tables)
    page = f'''<!doctype html><html><head><meta charset="utf-8"><title>{escape(name)}</title>
<style>body{{font:15px Arial;background:#f6f5f0;color:#263936;max-width:1400px;margin:30px auto;padding:20px}}
article{{background:white;border:1px solid #ddd;border-radius:14px;padding:20px;margin:20px 0}}
iframe{{width:100%;height:620px;border:0}}h2{{font-size:20px}}a{{color:#397d78}}p{{font-size:13px}}</style></head>
<body><h1>{escape(name)}</h1><p>{len(entries)} interactive graphs · {tables['mouse_summary'].height if 'mouse_summary' in tables else 0} mice</p>
<p>{links}</p>{''.join(cards)}</body></html>'''
    (out / 'report.html').write_text(page, encoding='utf8')


def run(config_path: str | Path, only: list[str] | None = None, *, configuration: dict | None = None) -> Path:
    config_path = Path(config_path).resolve()
    cfg = configuration if configuration is not None else load_config(config_path)
    for d in cfg['datasets']:
        path = Path(d['path']).expanduser()
        d['path'] = str((config_path.parent / path).resolve() if not path.is_absolute() else path.resolve())
    datasets = load_datasets(cfg['datasets'])
    settings = cfg['settings']
    selection = cfg.get('selection') or {}
    groups = selection.get('groups', cfg.get('groups') or list(dict.fromkeys(d.group for d in datasets)))
    if not groups:
        raise ValueError('Select at least one group')
    cfg['groups'] = groups
    name = cfg.get('run_name') or 'IntelliCage analysis'
    out = (config_path.parent / cfg.get('output_dir', 'results') / _slug(name)).resolve()
    if any(out.is_relative_to(d.path) or d.path.is_relative_to(out) for d in datasets) or config_path.is_relative_to(out):
        raise ValueError('Output and input/configuration folders must not overlap')
    if out.exists() and not (out / 'run_info.json').is_file():
        raise ValueError('Refusing to replace a folder that is not an analysis run')
    print('Reading exports and computing tables ...', flush=True)
    tables = build_tables(datasets, settings)
    analyses = analyses_for(cfg, tables)
    colors = P.colors_for(groups, cfg.get('colors'))
    starts = {(d.group, d.lot, d.phase): d.start for d in datasets}
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{out.name}-', dir=out.parent))
    backup = None
    try:
        for directory in ['tables', 'chart_data', 'stats', 'figures/html', 'figures/json', 'figures/png']:
            (stage / directory).mkdir(parents=True)
        selected_tables = {name: select_data(table, {}, groups, selection) for name, table in tables.items()}
        for table_name, table in selected_tables.items():
            table.write_csv(stage / 'tables' / f'{table_name}.csv')
        from .inference import compute_results
        prepared = None
        if settings.get('family_scope', 'graph') != 'graph':
            phases = sorted({phase for table in selected_tables.values() for phase in table['phase'].unique()})
            prepared = {phase: compute_results(analyses, tables, groups, colors, settings, starts,
                        {**selection, 'phases': [phase]}) for phase in phases}
        entries = []
        for a in analyses:
            label = a.get('name') or a.get('value') or a.get('plot')
            if only and not ({label, a.get('value'), analysis_name(a)} & set(only)):
                continue
            print(f'  {a.get("title", label)}', flush=True)
            entries.extend(_run_analysis(a, tables, groups, colors, settings, stage, starts, selection, cfg.get('styles'), prepared))
        _report(stage, name, entries, datasets, selected_tables)
        info = run_metadata(datasets, cfg)
        info['graphs'] = len(entries)
        (stage / 'run_info.json').write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding='utf8')
        (stage / 'config_used.yaml').write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf8')
        if out.exists():
            backup = out.with_name(f'.{out.name}.previous-{uuid4().hex}')
            out.rename(backup)
        stage.rename(out)
        if backup:
            shutil.rmtree(backup)
    except BaseException:
        if backup and backup.exists() and not out.exists():
            backup.rename(out)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f'Done: {out / "report.html"}', flush=True)
    return out


def describe(config_path, *, configuration=None):
    cfg = configuration if configuration is not None else load_config(Path(config_path))
    for name, table in build_tables(load_datasets(cfg['datasets']), cfg['settings']).items():
        print(f'{name}:\n    ' + '\n    '.join(c for c in table.columns if c not in M.IDS))
