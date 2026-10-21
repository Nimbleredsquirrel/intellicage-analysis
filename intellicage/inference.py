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
    from .graph_layout import KEY, fit_figure

    fig, stats = result['figure'], result['stats']
    fig.layout.annotations = tuple(n for n in fig.layout.annotations if n.name not in ('stat', 'sample-size', 'analysis-summary'))
    fig.layout.shapes = tuple(s for s in fig.layout.shapes if s.name != 'stat-bracket')
    groups = list(dict.fromkeys(t.meta['group'] for t in fig.data if isinstance(t.meta, dict) and t.meta.get('group')))
    comparisons = stats.get('posthoc', stats.get('test', pl.DataFrame()))
    marks = []
    paired = a.get('plot') == 'period_timecourse' and settings.get('period_style') == 'paired'
    days = sorted(stats.get('paired_values', pl.DataFrame({'day': []}))['day'].unique()) if paired else []
    means = next((t for t in fig.data if isinstance(t.meta, dict) and t.meta.get('role') == 'mean'), None)
    categories = list(means.x) if means is not None else []
    grouped = any(t.type == 'bar' for t in fig.data) and bool(a.get('by'))
    for row in comparisons.to_dicts():
        if row.get('group_a') == 'all': continue
        label = significance_label(row.get('p_adj', row.get('p')), settings.get('significance', 'stars'), settings.get('show_ns', True))
        if not label: continue
        ga, gb = row['group_a'], row['group_b']
        level = row.get('day', row.get('level'))
        xref, yref, x0, x1 = 'x', 'y', None, None
        if paired:
            if level not in days: continue
            axis = days.index(level) + 1
            suffix = str(axis) if axis > 1 else ''
            xref, yref = 'x' + suffix, 'y' + suffix
            if row['contrast'] == 'within mouse':
                x0, x1 = groups.index(ga) * 3, groups.index(ga) * 3 + 1
            else:
                periods = list(dict.fromkeys(stats['paired_values']['period_a'].to_list() + stats['paired_values']['period_b'].to_list()))
                offset = periods.index(row['period'])
                x0, x1 = groups.index(ga) * 3 + offset, groups.index(gb) * 3 + offset
            x = (x0 + x1) / 2
            position = (x + .5) / max(2, len(groups) * 3 - 1)
        elif level is None:
            x0, x1 = groups.index(ga), groups.index(gb)
            x = (x0 + x1) / 2
            position = (x + .5) / max(1, len(groups))
        else:
            by = a.get('by')
            if grouped:
                category = next((i for i, c in enumerate(sorted(result['data'][by].unique())) if str(c) == str(level)), None)
                if category is None: continue
                x = categories[category]
                step = .8 / max(1, len(groups))
                x0 = category + (groups.index(ga) - (len(groups) - 1) / 2) * step
                x1 = category + (groups.index(gb) - (len(groups) - 1) / 2) * step
                position = (category + .5) / max(1, len(categories))
            else:
                x = next((v for v in result['data'][by].unique() if str(v) == str(level)), level)
                levels = sorted(result['data'][by].unique())
                position = (levels.index(x) + .5) / len(levels)
                if 'period' in row:
                    identity = ga + ' · day/night' if row['contrast'] == 'within mouse' else row['period']
                    label = f'{identity}<br>{label}'
                elif len(groups) > 2:
                    label = f'{ga} vs {gb}<br>{label}'
        hover = f"{ga} vs {gb}" + (f" · {row['period']}" if 'period' in row else '')
        hover += f"<br>{row.get('test', '')}<br>raw {S.fmt_p(row.get('p'))}<br>adjusted {S.fmt_p(row.get('p_adj', row.get('p')))}"
        fig.add_annotation(name='stat', text=label, x=x, xref=xref, y=.95, yref=yref + ' domain',
                           showarrow=False, hovertext=hover, align='center', font={'size': 12})
        marks.append({'index': len(fig.layout.annotations) - 1, 'text': label, 'xref': xref, 'yref': yref,
                      'x0': x0, 'x1': x1, 'position': position, 'order': len(marks),
                      'series': f"{ga}|{gb}|{row.get('period', '')}|{row.get('contrast', '')}",
                      'bracket_span': (abs(x1 - x0) / (max(2, len(groups) * 3 - 1) if paired else
                          max(1, len(categories)) if grouped else max(1, len(groups)))) if x0 is not None else 0})
    if settings.get('show_n', True) and 'sample_sizes' in stats:
        parts = []
        for group in stats['sample_sizes']['group'].unique(maintain_order=True):
            counts = stats['sample_sizes'].filter(group=group)['n']
            count = str(counts.min()) if counts.min() == counts.max() else f'{counts.min()}–{counts.max()}'
            parts.append(f'{group}: n={count}')
        text = 'mice per point · ' + ' · '.join(parts)
        if paired: text = 'complete mouse pairs · n is shown for each group and cycle'
        fig.add_annotation(name='sample-size', text=text, showarrow=False)
    if settings.get('show_test_summary', True):
        lines = []
        omnibus = stats.get('omnibus', pl.DataFrame())
        if omnibus.height:
            test = omnibus['test'][0]
            lines.append(test + ' · model P values')
            lines.extend(f"{r['effect']}: {S.fmt_p(r['p'])}" for r in omnibus.to_dicts())
        elif comparisons.height:
            rows = [r for r in comparisons.to_dicts() if r.get('group_a') != 'all']
            if rows and not a.get('by'):
                lines.extend(f"{r['group_a']} vs {r['group_b']}: {S.fmt_p(r.get('p_adj', r.get('p')))} · {r['test']}" for r in rows)
        if a.get('value') and a.get('plot') != 'latency':
            lines.append('mean ± ' + {'sem': 'SEM', 'sd': 'SD', 'ci95': '95% CI'}[a.get('error', settings['error'])])
        if comparisons.height and a.get('by'):
            methods = ', '.join(str(v) for v in comparisons['correction'].drop_nulls().unique()) if 'correction' in comparisons.columns else settings['padjust']
            lines.append(f"pairwise: {methods} · family: {settings.get('family_scope', 'graph')} · hover labels for P values")
        if lines: fig.add_annotation(name='analysis-summary', text='<br>'.join(lines), showarrow=False)
    meta = dict(fig.layout.meta or {})
    meta[KEY] = {**meta.get(KEY, {}), 'marks': marks}
    fig.update_layout(meta=meta, uirevision=analysis_name(a))
    fit_figure(fig)


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
            results[name] = render_analysis(a, tables[a['table']], groups, colors, settings, starts, selection, decorate=False)
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
