# IntelliCage Analysis

[![Checks](https://github.com/Nimbleredsquirrel/intellicage-analysis/actions/workflows/checks.yml/badge.svg)](https://github.com/Nimbleredsquirrel/intellicage-analysis/actions/workflows/checks.yml)
![Python 3.12–3.14](https://img.shields.io/badge/python-3.12%E2%80%933.14-blue)

IntelliCage Analysis is a local desktop application and Python analysis pipeline for mouse behavioral experiments. Load export folders, label experimental groups and phases, explore individual mice, compare cohorts, and assemble figures into a dashboard. Save the inputs and analysis state together in a portable `.icage` project, or run the same analysis from the command line.

The goal is to make the path from raw exports to figures repeatable: every graph is backed by mouse-level data, explicit statistical settings, and a record of the source files used. Original export files are only read.

[Quickstart](#quickstart) · [First analysis](#your-first-analysis) · [Input format](#input-format) · [Batch analysis](#batch-analysis) · [Statistics](#statistics-and-analysis-conventions) · [Development](#development)

## What you can do

- **Explore behavior:** visit, nosepoke, and lick counts and durations; corner preferences; learning curves; first-visit latencies; hourly and day/night activity.
- **Analyze AnimalGate records:** session counts and durations, body weight, first-session timing, and session rasters when gate data are available.
- **Control the cohort:** select groups and a phase, exclude individual mice, and apply the same selection to figures, tables, and tests.
- **Refine figures:** edit titles, axes, colors, traces, comparison brackets, and graph sizes; arrange selected graphs on a dashboard.
- **Export results:** interactive HTML, PNG and SVG figures, a dashboard PDF, CSV/TXT/XLSX tables, and tables arranged for GraphPad Prism.
- **Keep work reproducible:** save data, labels, exclusions, settings, figure edits, and dashboard layout in a `.icage` project; generate batch reports with input checksums and code provenance.

## Quickstart

### Install from source

You need **Python 3.12, 3.13, or 3.14** and **[Poetry](https://python-poetry.org/docs/#installation) 2.2 or newer, below 3.0**. CI runs the test suite on Python 3.14. Dependencies are pinned in [`poetry.lock`](poetry.lock).

```bash
git clone https://github.com/Nimbleredsquirrel/intellicage-analysis.git
cd intellicage-analysis
poetry sync --with dev --all-extras
poetry run python app.py
```

This opens the desktop application. To use a local browser instead:

```bash
poetry run python app.py --browser --port 8080
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Both modes run on your computer and bind to the loopback address.

**Image and PDF exports require Google Chrome.** Install Chrome and open it once before exporting PNG, SVG, or dashboard PDF files. HTML graphs and tabular exports work without it. If the native desktop window cannot start, use browser mode.

### Desktop builds

The [desktop build workflow](https://github.com/Nimbleredsquirrel/intellicage-analysis/actions/workflows/desktop.yml) packages Windows, macOS Apple Silicon, and macOS Intel applications. Download the matching ZIP artifact from a successful run and extract the entire archive before launching the application. Packaged builds do not require a separate Python installation; Chrome is still required for image and PDF export.

## Your first analysis

1. **Add exports.** In desktop mode, use the **+** beside **your data**. You can select a parent folder containing multiple exports. In browser mode, choose an export folder and click **load folder**, or upload a ZIP archive.
2. **Label each dataset.** Enter its experimental **group**, **phase**, and optional **lot**. Labels can also come from an `intellicage.json` file inside each export folder.
3. **Choose the cohort.** Select the groups, one phase, and the mice to include. Check the export warnings in the sidebar before interpreting results.
4. **Explore the graphs and tables.** Switch between **overview**, **hourly activity**, and **day / night**. Adjust observation windows and statistical settings as needed.
5. **Build and export a dashboard.** Use **add to dashboard** or **choose graphs**, arrange and resize the panels, then export a **PDF report**. Individual graphs and tables have their own export controls.
6. **Save the project.** **save project** creates an `.icage` file containing the source text exports and your analysis state. Reopen it to continue editing, or use it as input to a batch run.

## Input format

Each dataset is an IntelliCage export folder with the following files. Names are case-sensitive on systems that enforce case sensitivity.

| File | Required | Used for |
| --- | --- | --- |
| `Animal.txt` | Yes | Animal IDs and cohort membership |
| `Visit.txt` | Yes | Visit timing, corners, durations, and visit-level counts |
| `Nosepoke.txt` | Yes | Nosepoke timing, durations, licks, and correct/incorrect labels |
| `Sessions.txt` | Yes | Recording intervals, phase boundaries, and observation coverage |
| `AnimalGate/Session.txt` | No | Gate sessions and body weight |
| `intellicage.json` | No | Experimental group, lot, and phase labels |

The `.txt` inputs must be **tab-separated IntelliCage exports** with the fields expected by the [loader](intellicage/io.py). Preserve the original filenames, column names, date/time fields, and `AnimalGate` subfolder. Renaming another export schema's files is not sufficient to make it compatible.

For example, an optional `intellicage.json` can contain:

```json
{
  "group": "control",
  "lot": "cohort-1",
  "phase": "place-learning"
}
```

Group and phase labels are required before analysis; lot may be empty. Labels entered in the project or YAML configuration override the corresponding folder metadata. Each export needs a unique path and a unique `(group, lot, phase)` combination. Use lot labels to distinguish cohorts that reuse animal IDs.

The loader checks required fields, numeric and time values, unique IDs, valid corners, and links between visits, nosepokes, and animals. It also reports animals with no visits and discrepancies between visit-level and nosepoke-level totals.

## Analysis approach

The desktop interface and batch runner share the same loading, metric, selection, and inference code. Export records are normalized into Polars tables, tagged with experimental metadata, and aggregated by mouse. Selected data then feed both the statistical tests and Plotly figures.

| Analysis | Examples | Computed tables |
| --- | --- | --- |
| Overall behavior | Counts, total and mean durations, visits with nosepokes, nosepokes with licks, targeted exploration | `mouse_summary` |
| Corner use | Visit share, time share, total time, and mean visit duration for corners 1–4 | `corner_summary` |
| Learning | Correct/incorrect nosepokes, daily error percentages, and thirst index | `mouse_summary`, `learning_by_day` |
| Activity | Clock-aligned and phase-aligned hourly activity, clock-hour profiles, and custom day/night windows | `activity_hourly`, `activity_elapsed`, `activity_circadian`, `activity_periods`, `activity_light_dark` |
| First visits | Latency to any corner or each specific corner, censoring, and first-visit duration | `latency` |
| AnimalGate | Session timing, counts, durations, weight, and visits per gate session | `gate_sessions`, `gate_summary`, `gate_hourly` |

Learning is detected from `Correct` or `Incorrect` values in `SideCondition`; it can also be set explicitly per dataset. Gate analyses are available only when gate records exist. The default graph catalogue is generated from the available tables and columns.

### Statistics and analysis conventions

**The unit of analysis is the mouse.** Mouse identity uses group, lot, and animal ID. Interactive comparisons use one phase at a time; batch runs generate separate figures for each selected phase.

| Setting | Default | Other supported choices |
| --- | --- | --- |
| Two-group independent test | Welch's t test | Student's t test, Mann–Whitney, automatic selection |
| Repeated-measures model | Mixed ANOVA | Linear mixed model (`lmm`) |
| Repeated-measures post hoc test | Welch's t test | Student's t test, Mann–Whitney |
| Configurable multiple-comparison adjustment | Šidák | Holm, Bonferroni, Benjamini–Hochberg FDR |
| Comparison family | Per graph | Per section or all configured graphs, separately per phase |
| Error bars | SEM | SD or pointwise 95% confidence intervals |
| Day/night windows | 08:00–20:00 / 20:00–08:00 | Custom non-overlapping windows, including date-specific schedules |
| First-visit observation window | Full phase | A positive number of minutes from phase start |
| Minimum recorded time per learning day | 12 hours | Configurable up to 24 hours |

For more than two independent groups, the implementation supports one-way ANOVA with Tukey comparisons or Kruskal–Wallis with Holm-adjusted Mann–Whitney comparisons. Broader comparison families apply the configured adjustment across their pairwise tests. Exported statistics identify the test, correction, and family used.

Several conventions matter when interpreting the output:

- **Repeated observations stay linked to mice.** Mixed ANOVA uses complete subjects and reports exclusions. The LMM option uses a mouse random intercept and retains available repeated observations.
- **Missing values remain missing.** Undefined ratios are not replaced with zero. Prism exports retain empty cells and subject mappings; sample-size tables report available and missing mouse values.
- **Recording coverage comes from `Sessions.txt`.** Activity tables include actual observation hours and recording gaps. Events, including their durations, are assigned to the bin containing their start time.
- **Unobserved first visits are right-censored.** Latency comparisons use log-rank tests, and continuous follow-up stops at the first recording gap. First-visit duration uses the whole visit when fully recorded.
- **Learning days are 24-hour intervals from phase start.** Day/night cycles follow the configured clock windows; windows may cross midnight. The default day/night graphs omit incomplete phase-edge windows, and the aggregate light/dark table excludes windows with incomplete recording coverage.

Graph notes and statistics tables expose unavailable tests, model warnings, censoring, and selection details. See [`stats.py`](intellicage/stats.py), [`timebins.py`](intellicage/timebins.py), and [`inference.py`](intellicage/inference.py) for the implementation.

## Batch analysis

### Run a saved project

Use the same inputs, selection, settings, and figure styles saved in the application:

```bash
poetry run python -m intellicage study.icage
```

### Run from YAML

The checked-in [`config.yaml`](config.yaml) supplies application defaults but has no dataset paths. For a batch run, save a complete configuration such as the following as `study.yaml` in the repository root, replacing the example paths and labels with your own:

```yaml
run_name: place-learning
output_dir: results
groups: [control, treatment]

datasets:
  - path: data/control
    group: control
    lot: cohort-1
    phase: place-learning
  - path: data/treatment
    group: treatment
    lot: cohort-2
    phase: place-learning

settings:
  light_on: 8
  light_off: 20
  period_windows:
    - {name: day, start: '08:00', end: '20:00'}
    - {name: night, start: '20:00', end: '08:00'}
  test: welch
  repeated_test: mixed_anova
  padjust: sidak
  family_scope: graph
  error: sem
  min_day_hours: 12
  latency_window_min: null
  include_partial_periods: false
  export_images: false

analyses: all
```

Run these commands from the repository root:

```bash
# Generate the report and all available default analyses
poetry run python -m intellicage study.yaml

# Inspect the computed tables and metric column names
poetry run python -m intellicage study.yaml --list

# Generate graphs matching selected metric columns or analysis names
poetry run python -m intellicage study.yaml --only visits_n incorrect_pct
```

Batch runs resolve relative dataset and output paths against the configuration file's directory. For `--list`, use absolute dataset paths if running from another directory. To preload a YAML configuration in the application, use `poetry run python app.py --config study.yaml`.

For a smaller report, replace `analyses: all` with explicit graph definitions:

```yaml
analyses:
  - name: total_visits
    table: mouse_summary
    value: visits_n
    title: Total visits
    ylabel: Visits (n)
  - name: learning_errors
    table: learning_by_day
    value: incorrect_pct
    by: day
    title: Learning errors
    ylabel: Incorrect nosepokes (%)
```

Each definition selects a computed table and metric, with an optional repeated factor (`by`), filters (`filter`), and statistical overrides. Set `stats: false` on a graph to omit inferential tests. See the [default catalogue](intellicage/catalogue.py) for examples.

### Batch outputs

The example above writes to `results/place-learning/`. Open `report.html` to browse the results.

| Path | Contents |
| --- | --- |
| `report.html` | Overview linking graphs, data, and analysis notes |
| `tables/*.csv` | Computed tables for the selected cohort |
| `chart_data/*.csv` | Data used by each graph |
| `stats/*.csv` | Descriptive statistics, sample sizes, tests, and comparison metadata, as applicable |
| `prism_tables/` | Prism-layout CSVs with JSON metadata and, where applicable, labelled copies and subject mappings |
| `figures/html/` | Interactive figures and their local Plotly JavaScript dependency |
| `figures/json/` | Plotly figure definitions |
| `figures/png/` | Static figures when `export_images: true` |
| `config_used.yaml` | Configuration used for the run |
| `run_info.json` | Input SHA-256 hashes, recording dates, animal IDs, settings, package versions, and code provenance |

Keep the report directory together so its relative links continue to work. Re-running the same configuration replaces its previous analysis directory after the new run succeeds. Input and output directories must not overlap.

Prism exports contain data arranged for import into Column or Grouped tables; they do not create a native Prism project. Missing measurements remain empty, and companion metadata preserves subject identities and layout information.

## Development

Install the development environment and run the existing suite:

```bash
poetry sync --with dev --all-extras
poetry run pytest
```

The suite covers import validation, behavioral metrics, time windows, statistics, Prism tables, project round trips, reports, and UI interactions. Pytest enforces a **95% coverage threshold**. Tests marked `real_data` depend on local experimental exports and are skipped when those folders are unavailable; synthetic export fixtures support the portable tests.

| Area | Source |
| --- | --- |
| Loading and dataset labels | [`io.py`](intellicage/io.py), [`metadata.py`](intellicage/metadata.py) |
| Metrics and observation windows | [`metrics.py`](intellicage/metrics.py), [`timebins.py`](intellicage/timebins.py) |
| Statistical tests and inference | [`stats.py`](intellicage/stats.py), [`inference.py`](intellicage/inference.py) |
| Graph definitions and plotting | [`catalogue.py`](intellicage/catalogue.py), [`plots.py`](intellicage/plots.py) |
| Projects and batch reports | [`project.py`](intellicage/project.py), [`pipeline.py`](intellicage/pipeline.py) |
| Application and dashboard | [`interface.py`](intellicage/interface.py), [`board.py`](intellicage/board.py) |
| Prism interchange | [`prism.py`](intellicage/prism.py) |

Report bugs and propose improvements through [GitHub Issues](https://github.com/Nimbleredsquirrel/intellicage-analysis/issues). Include the application mode, operating system, steps to reproduce, and any error report. For changes to metrics or statistics, include a small synthetic example and the expected result.

## License

This repository currently does not include a license file. Contact the repository owner for reuse and redistribution terms.
