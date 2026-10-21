from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import polars as pl

from . import stats as S
from .catalogue import analysis_name, analysis_section


def significance_label(p, mode='p', show_ns=True):
    if mode == 'none' or p is None or not np.isfinite(p):
        return ''
    if not show_ns and p > .05:
        return ''
    return {'p': S.fmt_p(p), 'stars': S.stars(p), 'both': f'{S.stars(p)}<br>{S.fmt_p(p)}'}[mode]


def annotate(a, result, settings):
    fig, stats = result['figure'], result['stats']
    fig.layout.annotations = tuple(note for note in fig.layout.annotations if note.name not in ('stat', 'sample-size'))
    comparisons = stats.get('posthoc', stats.get('test', pl.DataFrame()))
    mode, show_ns = settings.get('significance', 'p'), settings.get('show_ns', False)
    labels = {}
    for row in comparisons.to_dicts():
        if row.get('group_a') == 'all':
            continue
        text = significance_label(row.get('p_adj', row.get('p')), mode, show_ns)
        if not text:
            continue
        prefix = f"{row.get('group_a', '')} vs {row.get('group_b', '')}"
        if 'period' in row:
            prefix = f"{row['group_a']} · {row['period']}" if row['contrast'] == 'within mouse' else f"{prefix} · {row['period']}"
        x = row.get('day', row.get('level'))
        labels.setdefault(x, []).append(f'{prefix}: {text}')
    paired = a.get('plot') == 'period_timecourse' and settings.get('period_style') == 'paired'
    days = sorted(result['stats'].get('paired_values', pl.DataFrame({'day': []}))['day'].unique()) if paired else []
    for level, texts in labels.items():
        options = {'xref': 'paper', 'x': .5, 'yref': 'paper', 'y': 1.08}
        if paired:
            if level not in days:
                continue
            axis = days.index(level) + 1
            suffix = str(axis) if axis > 1 else ''
            options = {'xref': f'x{suffix} domain', 'yref': f'y{suffix} domain', 'x': .5, 'y': 1, 'yanchor': 'bottom', 'yshift': 22}
        elif level is not None:
            by = a.get('by')
            if by == 'corner':
                x = f'Corner {level}'
            else:
                x = next((v for v in result['data'][by].unique() if str(v) == str(level)), level)
            options = {'x': x, 'yref': 'paper', 'y': 1.03}
        fig.add_annotation(name='stat', text='<br>'.join(texts), showarrow=False, font={'size': 10}, **options)
    if settings.get('show_n', True) and 'sample_sizes' in stats:
        parts = []
        for group in stats['sample_sizes']['group'].unique(maintain_order=True):
            counts = stats['sample_sizes'].filter(group=group)['n']
            count = str(counts.min()) if counts.min() == counts.max() else f'{counts.min()}–{counts.max()}'
            parts.append(f'{group}: n={count}')
        text = 'mice per point · ' + ' · '.join(parts)
        if paired:
            text = 'complete mouse pairs · n is shown for each group and cycle'
        fig.add_annotation(name='sample-size', text=text, xref='paper', yref='paper', x=0, y=-.32,
                           xanchor='left', showarrow=False, font={'size': 10})
        fig.update_layout(margin_b=max(fig.layout.margin.b or 0, 130))


def apply_families(results, analyses, settings):
    definitions = {analysis_name(a): a for a in analyses}
    scope = settings.get('family_scope', 'graph')
    families, comparison_tables = {}, {}
    for name, result in results.items():
        a = definitions[name]
        family = name if scope == 'graph' else ' / '.join(analysis_section(a)) if scope == 'section' else 'all analyses'
        for kind in ('test', 'posthoc'):
            table = result['stats'].get(kind, pl.DataFrame())
            rows = table.to_dicts()
            comparison_tables[name, kind] = (table.schema, rows)
            for index, row in enumerate(rows):
                if row.get('group_a') == 'all':
                    continue
                families.setdefault(family, []).append((name, kind, index, row))
    for family, members in families.items():
        method = settings['padjust']
        adjusted = S.adjust_pvalues([row.get('p') if row.get('p') is not None else np.nan
                                    for _, _, _, row in members], method) if scope != 'graph' else None
        for i, (name, kind, index, row) in enumerate(members):
            if adjusted is not None:
                row['p_adj'] = float(adjusted[i])
                row['summary'] = S.stars(row['p_adj'])
                row['correction'] = method
            row.update(family_id=family, family_scope=scope, family_size=len(members),
                       family_phase=', '.join(str(p) for p in results[name]['data']['phase'].unique().sort()))
            row['family_input'] = 'raw ANOVA pooled t p' if 'Tukey' in row.get('test', '') else 'raw p'
            if scope == 'graph':
                row['correction'] = ('tukey' if 'Tukey' in row.get('test', '') else 'holm' if 'Kruskal' in row.get('test', '')
                                     else definitions[name].get('padjust', method))
    for (name, kind), (schema, rows) in comparison_tables.items():
        if rows:
            results[name]['stats'][kind] = pl.DataFrame(rows, schema_overrides=schema)
    for name, result in results.items():
        if scope != 'graph' and any(k in result['stats'] for k in ('test', 'posthoc')):
            result['notes'].append(f'Pairwise comparisons: {settings["padjust"]}, family = {scope}, separately per phase. Omnibus model tests are separate. Shared correction replaces graph-specific correction. ANOVA contrasts use raw pooled t P values.')
        if 'test' in result['stats']:
            result['tests'] = result['stats']['test'].to_dicts()
        annotate(definitions[name], result, settings)


def compute_results(analyses, tables, groups, colors, settings, starts, selection=None):
    from .pipeline import render_analysis

    results = {}
    for a in analyses:
        if a['table'] not in tables:
            continue
        name = analysis_name(a)
        try:
            results[name] = render_analysis(a, tables[a['table']], groups, colors, settings, starts, selection)
        except ValueError as exc:
            if str(exc) not in {'No data for this selection', 'No common observations for the selected groups'}:
                raise
            from .selection import select_data
            figure = go.Figure()
            figure.add_annotation(text=str(exc), showarrow=False, x=.5, y=.5, xref='paper', yref='paper')
            figure.update_layout(title_text=a.get('title', name), template='simple_white', height=540)
            results[name] = {'figure': figure, 'stats': {}, 'tests': [], 'notes': [str(exc)],
                             'data': select_data(tables[a['table']], a, groups, selection)}
    apply_families(results, analyses, settings)
    return results


def sample_sizes(eligible, a):
    keys = ['group'] + ([a['by']] if a.get('by') else [])
    if a.get('plot') == 'period_timecourse' and 'period' not in keys:
        keys.append('period')
    mouse = pl.struct('lot', 'phase', 'animal')
    cohort = eligible.group_by('group').agg(n_total=mouse.n_unique())
    available = pl.col(a['value']).is_finite()
    if a.get('plot') == 'latency':
        available = available | pl.col(a['value']).is_null()
    counts = eligible.group_by(keys).agg(n=mouse.filter(available).n_unique())
    if len(keys) > 1:
        bins = eligible.select(keys[1:]).unique()
        counts = cohort.join(bins, how='cross').join(counts, on=keys, how='left').with_columns(pl.col('n').fill_null(0))
    else:
        counts = counts.join(cohort, on='group')
    return counts.with_columns(missing=pl.col('n_total') - pl.col('n')).sort(keys)
