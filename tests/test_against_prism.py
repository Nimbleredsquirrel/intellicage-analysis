from pathlib import Path

import polars as pl
import pytest

from intellicage import metrics as M
from intellicage import stats as S
from intellicage.io import load_dataset

DOCS = Path("~/Documents").expanduser()
CTRL, KO = DOCS / "Phase 3 Lot 5 control", DOCS / "Phase 3 Lot 4 FMR1 KO"
pytestmark = [pytest.mark.real_data,
              pytest.mark.skipif(not (CTRL.exists() and KO.exists()), reason="export folders not found")]
G = ["Contrôle", "FMR1 KO"]


@pytest.fixture(scope="module")
def data():
    c = load_dataset(CTRL, group="Contrôle", lot="Lot 5", phase="Phase 3")
    k = load_dataset(KO, group="FMR1 KO", lot="Lot 4", phase="Phase 3")
    return c, k


def _mouse(df, animal, col):
    return df.filter(pl.col("animal") == animal)[col][0]


def test_mouse_summary_matches_prism(data):
    c, k = data
    s = pl.concat([M.mouse_summary(c), M.mouse_summary(k)])
    assert _mouse(s, 718, "visits_n") == 897
    assert _mouse(s, 700, "visits_n") == 721
    assert _mouse(s, 718, "visits_duration_total_s") == pytest.approx(7365.733)
    assert _mouse(s, 718, "visits_duration_mean_s") == pytest.approx(8.211519509)
    assert _mouse(s, 744, "nosepokes_n") == 2393
    assert _mouse(s, 744, "licks_n") == 44348
    assert _mouse(s, 700, "licks_duration_per_visit_s") == pytest.approx(3.793764845)


def test_corner_tables_match_prism(data):
    c, _ = data
    cs = M.corner_summary(c)
    row = cs.filter(pl.col("animal") == 742).sort("corner")
    assert row["visit_duration_mean_s"].to_list() == pytest.approx([13.76866, 9.978456522, 11.13988889, 10.50889216])
    assert row["visits_pct"].to_list() == pytest.approx([21.45922747, 19.74248927, 29.61373391, 29.18454936])


def test_learning_by_day_matches_prism(data):
    c, _ = data
    lb = M.learning_by_day(c).filter(pl.col("animal") == 742).sort("day")
    assert lb["day"].to_list() == list(range(1, 8))
    assert lb["incorrect_pct"].round(1).to_list() == [50.2, 53.1, 50.0, 45.7, 45.6, 54.5, 47.2]


@pytest.mark.parametrize("value,p", [("visits_n", 0.0599), ("nosepokes_n", 0.0488), ("licks_n", 0.0120),
                                     ("visits_duration_mean_s", 0.9946), ("licks_duration_per_visit_s", 0.9428)])
def test_welch_matches_prism(data, value, p):
    c, k = data
    s = pl.concat([M.mouse_summary(c), M.mouse_summary(k)])
    _, test = S.compare_groups(s, value, G, "welch")
    assert test["p"][0] == pytest.approx(p, abs=1e-4)


def test_inputs_untouched(data):
    for folder in (CTRL, KO):
        names = {p.name for p in folder.rglob("*") if p.name != ".DS_Store"}
        assert names == {"AnimalGate", "Group.txt", "Sessions.txt", "HardwareEvent.txt", "Nosepoke.txt", "Log.txt",
                         "Animal.txt", "Environment.txt", "Visit.txt", "Session.txt"}
