import math

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from intellicage.selection import present_groups, select_data


@pytest.fixture
def frame():
    return pl.DataFrame({"group": ["a", "a", "a", "b", "a", "a", "a"],
                         "lot": ["x", "x", "y", "x", "x", "x", "x"],
                         "phase": ["one", "two", "one", "one", "one", "one", "one"],
                         "animal": [1, 1, 1, 1, 2, 3, 4], "corner": [1, 1, 1, 2, 2, 3, None],
                         "y": [1., 2., 3., 4., None, math.nan, math.inf]})


def test_selection_is_pure_and_preserves_order(frame):
    original = frame.clone()
    got = select_data(frame, {"value": "y"}, ["a", "b"])
    assert got["y"].to_list() == [1., 2., 3., 4.]
    assert_frame_equal(frame, original)


def test_exclusions_use_full_mouse_identity_across_phases(frame):
    selection = {"exclude_mice": [{"group": "a", "lot": "x", "animal": 1}]}
    got = select_data(frame, {"value": "y"}, ["a", "b"], selection)
    assert got["y"].to_list() == [3., 4.]


def test_groups_phases_and_filters_intersect(frame):
    got = select_data(frame, {"value": "y", "by": "corner", "filter": {"corner": [1, 2]}}, ["a", "b"],
                      {"groups": ["a"], "phases": ["one"],
                       "exclude_mice": [{"group": "a", "lot": "x", "animal": 1}]})
    assert got["y"].to_list() == [3.]


@pytest.mark.parametrize("selection", [{"groups": []}, {"phases": []}, {"phases": ["absent"]}])
def test_empty_selection_keeps_schema(frame, selection):
    got = select_data(frame, {}, ["a", "b"], selection)
    assert got.is_empty()
    assert got.schema == frame.schema


@pytest.mark.parametrize("choice,expected", [(1, [1., 2., 3.]), ([2], [4., None]), ([], []),
                                             (None, [math.inf]), ([None, 1], [1., 2., 3., math.inf])])
def test_scalar_list_and_null_column_filters(frame, choice, expected):
    got = select_data(frame, {"filter": {"corner": choice}}, ["a", "b"])
    assert got["y"].to_list() == expected


def test_missing_within_level_is_removed(frame):
    got = select_data(frame, {"by": "corner"}, ["a", "b"])
    assert got.height == 6


def test_group_order_is_explicit(frame):
    assert present_groups(frame, ["b", "missing", "a"]) == ["b", "a"]


@pytest.mark.parametrize("analysis", [{"value": "missing"}, {"by": "missing"}, {"filter": {"missing": 1}}])
def test_unknown_columns_have_readable_errors(frame, analysis):
    with pytest.raises(ValueError, match="Missing columns: missing"):
        select_data(frame, analysis, ["a"])


def test_metric_must_be_numeric(frame):
    with pytest.raises(ValueError, match="must be numeric"):
        select_data(frame, {"value": "lot"}, ["a"])


@pytest.mark.parametrize("mouse", ["1 (a, x)", {"animal": 1}, {"group": "a", "lot": "x", "animal": "1"},
                                   {"group": "a", "lot": "x", "animal": True}])
def test_exclusion_labels_cannot_masquerade_as_ids(frame, mouse):
    with pytest.raises(ValueError, match="excluded mouse|animal ids"):
        select_data(frame, {}, ["a"], {"exclude_mice": [mouse]})


def test_gate_selection_does_not_require_a_metric(frame):
    got = select_data(frame, {"plot": "gate_raster", "filter": {"animal": 1}}, ["b"])
    assert got.select("group", "animal").rows() == [("b", 1)]
