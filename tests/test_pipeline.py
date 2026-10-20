import hashlib
import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
import yaml

from intellicage import plots as P
from intellicage.pipeline import DEFAULT_SETTINGS, _run_analysis, build_tables, describe, load_config, run


def test_full_run_produces_report_without_changing_exports(tmp_path, config, datasets):
    def fingerprint():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for d in datasets for p in d.path.rglob("*") if p.is_file()}

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    before = fingerprint()
    out = run(path)
    assert fingerprint() == before
    assert (out / "report.html").exists()
    assert len(list((out / "tables").glob("*.csv"))) == 12
    assert not list((out / "figures/png").glob("*.png"))
    assert len(list((out / "figures/html").glob("*.html"))) == 5
    assert (out / "figures/html/plotly.min.js").exists()
    assert yaml.safe_load((out / "config_used.yaml").read_text())["datasets"] == config["datasets"]
    assert json.loads((out / "run_info.json").read_text())["groups"] == config["groups"]
    assert "Visits" in (out / "report.html").read_text()
    assert (out / "prism_tables/mouse_summary_visits_n.csv").is_file()
    assert (out / "prism_tables/mouse_summary_visits_n__subjects.csv").is_file()


def test_batch_filters_gate_groups_mice_and_columns(tmp_path, config, datasets):
    config["selection"] = {"groups": ["control"], "phases": ["phase 1"],
                           "exclude_mice": [{"group": "control", "lot": "lot 1", "animal": 1}]}
    config["analyses"] = [{"table": "gate_sessions", "plot": "gate_raster", "filter": {"animal": [2]}}]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    out = run(path)
    assert len(list((out / "figures/html").glob("*.html"))) == 1
    html = (out / "figures/html/gate_sessions_gate_raster.html").read_text()
    assert "2 (control, lot 1)" in html
    assert "3 (control, lot 1)" not in html
    assert "(ko, lot 2)" not in html


@pytest.mark.parametrize("only,expected", [("visits_n", {"mouse_summary_visits_n.html", "activity_hourly_visits_n_hour.html"}),
                                           ("summary_visits", {"summary_visits.html"})])
def test_only_selects_by_metric_or_explicit_name(tmp_path, config, only, expected):
    if only == "summary_visits":
        config["analyses"][0]["name"] = only
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    out = run(path, only=[only])
    assert {p.name for p in (out / "figures/html").glob("*.html")} == expected


def test_missing_optional_tables_are_skipped(tmp_path, export_factory, capsys):
    path = export_factory(learning=False, gate=False)
    from intellicage.io import load_dataset

    tables = build_tables([load_dataset(path, group="control")], DEFAULT_SETTINGS)
    assert "learning_by_day" not in tables
    assert "gate_sessions" not in tables
    assert _run_analysis({"table": "gate_sessions", "plot": "gate_raster"}, tables,
                         ["control"], P.colors_for(["control"]), DEFAULT_SETTINGS, tmp_path, {}) == []
    assert "no data" in capsys.readouterr().out


def test_no_selected_values_yields_no_figure(tmp_path, tables):
    assert _run_analysis({"table": "mouse_summary", "value": "visits_n"}, tables, ["absent"],
                         {}, DEFAULT_SETTINGS, tmp_path, {}) == []


def test_multiple_phases_have_separate_figure_names_and_gate_origins(tmp_path, tables, datasets):
    from datetime import timedelta

    gate = tables["gate_sessions"].filter(group="control")
    later = gate.with_columns(pl.lit("phase 2").alias("phase"),
                              pl.col("start", "end") + timedelta(days=10))
    combined = pl.concat([gate, later])
    starts = {("control", "lot 1", "phase 1"): datasets[0].start,
              ("control", "lot 1", "phase 2"): datasets[0].start + timedelta(days=10)}
    for folder in ["stats", "figures/png", "figures/html", "figures/json", "chart_data"]:
        (tmp_path / folder).mkdir(parents=True)
    entries = _run_analysis({"table": "gate_sessions", "plot": "gate_raster"}, {"gate_sessions": combined},
                            ["control"], P.colors_for(["control"]), DEFAULT_SETTINGS, tmp_path, starts)
    assert [e["name"] for e in entries] == ["gate_sessions_gate_raster_phase_1", "gate_sessions_gate_raster_phase_2"]
    plot = P.gate_raster(combined, starts, ["control"], P.colors_for(["control"]), title="gate")
    assert min(x for trace in plot.data for x in trace.x if x is not None) >= 0
    assert max(x for trace in plot.data for x in trace.x if x is not None) < 4


@pytest.mark.parametrize("unsafe", ["inside", "ancestor", "empty_slug", "no_groups"])
def test_unsafe_output_paths_are_rejected_before_deletion(tmp_path, config, datasets, unsafe):
    if unsafe == "inside":
        config["output_dir"] = str(datasets[0].path)
    elif unsafe == "ancestor":
        config["output_dir"] = str(tmp_path.parent)
        config["run_name"] = tmp_path.name
    elif unsafe == "empty_slug":
        config["run_name"] = "!!!"
    else:
        config["selection"] = {"groups": []}
    sentinel = datasets[0].path / "keep.txt"
    sentinel.write_text("keep")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="overlap|ASCII|at least one group"):
        run(path)
    assert sentinel.read_text() == "keep"


def test_config_defaults_and_table_description(tmp_path, config, capsys):
    path = tmp_path / "config.yaml"
    config["settings"] = {"light_on": 8}
    path.write_text(yaml.safe_dump(config))
    loaded = load_config(path)
    assert loaded["settings"]["light_on"] == 8
    assert loaded["settings"]["light_off"] == 20
    describe(path)
    output = capsys.readouterr().out
    assert "mouse_summary:" in output and "visits_n" in output


def test_cli_lists_columns_and_has_help(tmp_path, config):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    for args in [["--help"], [str(path), "--list"]]:
        result = subprocess.run([sys.executable, "-m", "intellicage", *args], cwd=root,
                                capture_output=True, text=True, check=True)
        assert "visits_n" in result.stdout or "usage:" in result.stdout


@pytest.mark.parametrize('key,value,match', [('latency_window_min',0,'Latency'),('min_day_hours',0,'Minimum'),
    ('test','typo','independent'),('repeated_test','typo','repeated'),('padjust','typo','adjustment')])
def test_invalid_settings(key,value,match):
    from intellicage.pipeline import validate_settings
    with pytest.raises(ValueError,match=match): validate_settings({**DEFAULT_SETTINGS,key:value})


def test_invalid_config_and_duplicate_exports(tmp_path,config):
    from intellicage.pipeline import load_datasets, analyses_for
    path=tmp_path/'empty.yaml';path.write_text('[]')
    with pytest.raises(ValueError,match='at least one dataset'): load_config(path)
    with pytest.raises(ValueError,match='own path'): load_datasets([config['datasets'][0]]*2)
    with pytest.raises(ValueError,match='unique'):
        analyses_for({'analyses':config['analyses'][:1]*2}, {})


def test_phase_must_be_selected_before_rendering(tables,datasets):
    from intellicage.pipeline import render_analysis
    table=tables['mouse_summary']
    both=pl.concat([table,table.with_columns(pl.lit('phase 2').alias('phase'))])
    with pytest.raises(ValueError,match='one phase'):
        render_analysis({'table':'mouse_summary','value':'visits_n'},both,['control','ko'],{},DEFAULT_SETTINGS,{})


def test_failed_replacement_restores_previous_run(tmp_path,config,monkeypatch):
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    out=run(path)
    previous=(out/'report.html').read_bytes()
    real_rename=Path.rename
    def fail_stage(self,target):
        if self.name.startswith('.test_run-'):
            raise OSError('simulated interrupted replacement')
        return real_rename(self,target)
    monkeypatch.setattr(Path,'rename',fail_stage)
    with pytest.raises(OSError,match='interrupted'): run(path)
    assert (out/'report.html').read_bytes()==previous
    assert not list(out.parent.glob('.test_run*'))


def test_existing_non_run_folder_is_preserved(tmp_path,config):
    out=tmp_path/'results/test_run';out.mkdir(parents=True)
    (out/'personal.txt').write_text('keep')
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError,match='Refusing'): run(path)
    assert (out/'personal.txt').read_text()=='keep'


def test_batch_reproduces_saved_graph_styles(tmp_path,config):
    config['analyses']=config['analyses'][:1]
    config['styles']={'mouse_summary_visits_n': {'layout':{'title.text':'batch edit'},'traces':{}}}
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    out=run(path)
    assert json.loads((out/'figures/json/mouse_summary_visits_n.json').read_text())['layout']['title']['text']=='batch edit'


def test_cli_entrypoints(config,tmp_path,monkeypatch):
    import runpy
    import intellicage.interface
    from intellicage.__main__ import main
    calls=[]
    monkeypatch.setattr(intellicage.interface,'main',lambda argv:calls.append(argv))
    runpy.run_module('app',run_name='__main__')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'argv',['IntelliCage'])
    runpy.run_module('app',run_name='__main__')
    assert calls==[None,['--desktop']]
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(config))
    main([str(path),'--list'])
