from datetime import timedelta

import numpy as np
import polars as pl
import pytest

from intellicage import plots as P


def test_batch_png_export_keeps_layout_and_uses_high_resolution(tmp_path, monkeypatch):
    import plotly.graph_objects as go

    figure = go.Figure(go.Scatter(x=[1, 2], y=[2, 3]))
    figure.update_layout(width=840, height=420)
    exports = []
    monkeypatch.setattr(go.Figure, 'write_image', lambda self, path, **kwargs: exports.append((path, kwargs)))
    P.save(figure, tmp_path / 'graph.html', tmp_path / 'graph.png')
    assert (tmp_path / 'graph.html').is_file()
    assert exports == [(tmp_path / 'graph.png', {'width': 840, 'height': 420, 'scale': 4})]
    assert (figure.layout.width, figure.layout.height) == (840, 420)


@pytest.mark.parametrize("style", ["bar", "scatter"])
def test_column_graph_shows_each_mouse_mean_sem_and_pairwise_result(tables, style):
    data = tables["mouse_summary"]
    pairs = pl.DataFrame({"group_a": ["control"], "group_b": ["ko"], "p_adj": [.01],
                          "summary": ["*"], "test": ["Welch"]})
    plot = P.column_plot(data, "visits_n", ["control", "ko"], P.colors_for(["control", "ko"]),
                              title="Visits", ylabel="Visits (n)", pairwise=pairs, style=style, ylim=[0, 12])
    assert tuple(plot.layout.yaxis.range) == (0, 12)
    assert plot.data[0].y[0] == 4
    assert plot.data[0].error_y.array[0] == pytest.approx(1 / np.sqrt(3))
    assert [row[0] for row in plot.data[1].customdata] == [1, 2, 3]
    assert "P=0.0100" in plot.layout.annotations[0].text
    assert plot.layout.yaxis.title.text == "Visits (n)"


def test_column_graph_can_show_available_data_without_a_test(tables):
    from intellicage.stats import PAIRWISE_SCHEMA

    data = tables["mouse_summary"].filter(animal=1)
    plot = P.column_plot(data, "visits_n", ["control", "ko"], P.colors_for(["control", "ko"]),
                              title="Visits", ylabel="n", pairwise=pl.DataFrame(schema=PAIRWISE_SCHEMA))
    assert len(plot.data) == 4


def test_grouped_graph_has_four_corners_and_mouse_ids(tables):
    posthoc = pl.DataFrame({"level": ["1"], "summary": ["*"], "p_adj": [.02]})
    plot = P.grouped_plot(tables["corner_summary"], "time_s", "corner", ["control", "ko"],
                               P.colors_for(["control", "ko"]), title="Corners", ylabel="s",
                               category_labels={1: "Corner 1"}, posthoc=posthoc)
    assert list(plot.data[0].x) == ["Corner 1", "2", "3", "4"]
    assert len(plot.data[1].customdata) == 12
    assert plot.layout.annotations[0].text == "P=0.0200"


@pytest.mark.parametrize("points", [True, False])
def test_learning_graph_labels_and_adjusted_significance(tables, points):
    posthoc = pl.DataFrame({"level": ["1", "2"], "summary": ["ns", "**"], "p_adj": [.7, .004]})
    plot = P.timecourse_plot(tables["learning_by_day"], "incorrect_pct", "day", ["control", "ko"],
                                  P.colors_for(["control", "ko"]), title="Learning", ylabel="%", xlabel="Day",
                                  x_labels={1: "J1", 2: "J2"}, posthoc=posthoc, show_points=points)
    assert list(plot.layout.xaxis.ticktext) == ["J1", "J2"]
    assert len(plot.layout.annotations) == 1
    assert plot.layout.annotations[0].text == "**<br>P=0.0040"
    assert sum(trace.visible == "legendonly" for trace in plot.data) == (2 if points else 0)


def test_dense_activity_graph_shades_dark_hours(tables):
    plot = P.timecourse_plot(tables["activity_hourly"], "visits_n", "hour", ["control", "ko"],
                                  P.colors_for(["control", "ko"]), title="Activity", ylabel="Visits/h",
                                  xlabel="Hour", dark_col="dark", show_points=False)
    assert plot.layout.xaxis.dtick == 24
    assert len(plot.layout.shapes) > 0
    assert len(plot.data) == 2


def test_gate_rows_distinguish_reused_animal_ids_in_other_lots(tables, datasets):
    first = tables["gate_sessions"].filter(group="control")
    second = first.with_columns(pl.lit("other lot").alias("lot"))
    data = pl.concat([first, second])
    starts = {("control", "lot 1", "phase 1"): datasets[0].start,
              ("control", "other lot", "phase 1"): datasets[0].start}
    plot = P.gate_raster(data, starts, ["control"], P.colors_for(["control"]), title="Gate")
    assert len(plot.layout.yaxis.categoryarray) == 6
    assert "1 (control, lot 1)" in plot.layout.yaxis.categoryarray
    assert "1 (control, other lot)" in plot.layout.yaxis.categoryarray


def test_colours_cycle_and_allow_overrides():
    groups = [str(i) for i in range(8)]
    colors = P.colors_for(groups, {"1": "#000000"})
    assert colors["1"] == "#000000"
    assert colors["0"] == colors["6"]
    assert colors["0"] == "#2a78d6"
    assert P.colors_for(["control", "ko"])["ko"] == "#eb6834"
    assert P.PLOT_CONFIG["scrollZoom"] is False
