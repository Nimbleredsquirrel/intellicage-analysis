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


def statistics_controls(settings, on_apply=None):
    fields = {}
    for key, label, options in OPTIONS:
        fields[key] = ui.select(options, label=label, value=settings[key]).props(
            'outlined dense stack-label options-dense').classes('w-44 dashboard-selector')
    for key, label in [('show_n', 'show number of mice'), ('show_ns', 'show non-significant results'), ('show_test_summary', 'show test summary')]:
        fields[key] = ui.checkbox(label, value=settings[key])
    if on_apply:
        fields['significance'].on_value_change(lambda _: on_apply({key: field.value for key, field in fields.items()}))
    return fields
