from nicegui import ui


OPTIONS = [
    ('error', 'error bars', {'sem': 'SEM', 'sd': 'SD', 'ci95': '95% CI'}),
    ('bar_style', 'summary style', ['bar', 'scatter']),
    ('test', 'group test', ['welch', 'student', 'mann_whitney', 'auto']),
    ('repeated_test', 'repeated measures', ['mixed_anova', 'lmm']),
    ('padjust', 'multiple comparisons', ['sidak', 'holm', 'bonferroni', 'fdr_bh']),
    ('family_scope', 'comparison family', {'graph': 'this graph', 'section': 'this section', 'all': 'all configured graphs'}),
    ('significance', 'statistical labels', {'p': 'p-value', 'stars': 'Prism stars', 'both': 'stars + p-value', 'none': 'none'}),
]


def statistics_controls(settings):
    fields = {}
    for key, label, options in OPTIONS:
        fields[key] = ui.select(options, label=label, value=settings[key]).props(
            'outlined dense stack-label options-dense').classes('w-44 dashboard-selector')
    for key, label in [('show_n', 'show number of mice'), ('show_ns', 'show non-significant results')]:
        fields[key] = ui.checkbox(label, value=settings[key])
    return fields


def statistics_help():
    ui.label('Stars use the full adjusted p-value: * ≤ 0.05, ** ≤ 0.01, *** ≤ 0.001, **** ≤ 0.0001; ns > 0.05. Numerical p-values are always exported.').classes('muted')
    ui.label('Comparison families include pairwise tests in all configured graphs of the selected scope, separately for each phase. Dashboard cards do not change the family. Omnibus model tests are separate. 95% CI shows uncertainty in the mean; SD shows mouse-to-mouse spread.').classes('muted')
