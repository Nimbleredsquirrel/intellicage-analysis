from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from intellicage.io import load_dataset
from intellicage.pipeline import DEFAULT_SETTINGS, build_tables


@pytest.fixture
def export_factory(tmp_path):
    def make(group="control", lot="lot 1", phase="phase 1", *, learning=True, gate=True, extra=0):
        path = tmp_path / f"{group}-{lot}-{phase}"
        path.mkdir(parents=True)
        start = datetime(2026, 1, 1, 7, 30)
        end = start + timedelta(hours=48)
        pl.DataFrame({"Session": [1], "SessionStart": [str(start)], "SessionEnd": [str(end)]}).write_csv(
            path / "Sessions.txt", separator="\t")
        pl.DataFrame({"Animal": [1, 2, 3]}).write_csv(path / "Animal.txt", separator="\t")
        visits, pokes, gates = [], [], []
        visit_id = 0

        def times(t, seconds):
            stop = t + timedelta(seconds=seconds)
            return {"StartDate": str(t.date()), "StartTime": t.strftime("%H:%M:%S.%f")[:-3],
                    "EndDate": str(stop.date()), "EndTime": stop.strftime("%H:%M:%S.%f")[:-3]}

        for animal in (1, 2, 3):
            events = [(10, 1, 10., [("Correct", .5, 4), ("Incorrect", 1.5, 0)]),
                      (70, 2, 20., [("Incorrect", 1., 0)]),
                      (1500, 1, 30., [("Correct", 2., 0)])]
            events += [(1510 + j * 10, 3, 5. + j, [("Incorrect", 1., 0)])
                       for j in range(animal - 1 + extra)]
            for minute, corner, duration, ns in events:
                visit_id += 1
                t = start + timedelta(minutes=minute + animal - 1)
                visits.append({"VisitID": visit_id, "Animal": animal, "Corner": corner,
                               "VisitDuration": duration, "NosepokeNumber": len(ns),
                               "NosepokeDuration": sum(n[1] for n in ns), "LickNumber": sum(n[2] for n in ns),
                               "LickDuration": 2. if any(n[2] for n in ns) else 0.,
                               "LickContactTime": 1. if any(n[2] for n in ns) else 0., **times(t, duration)})
                for order, (condition, seconds, licks) in enumerate(ns):
                    pokes.append({"VisitID": visit_id, "NosepokeOrder": order + 1, "Animal": animal,
                                  "Corner": corner, "SideCondition": condition if learning else "Unscored",
                                  "NosepokeDuration": seconds, "LickNumber": licks,
                                  "LickDuration": 2. if licks else 0., "LickContactTime": 1. if licks else 0.,
                                  **times(t + timedelta(seconds=order), seconds)})
            for j in range(animal):
                t = start + timedelta(minutes=2 + animal + j * 60)
                for direction, seconds, weight in [("Out", 10, 20. + animal), ("In", 60 + j * 10, -1.)]:
                    gates.append({"SessionId": len(gates) + 1, "Animal": animal,
                                  "Start": t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                                  "End": (t + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                                  "Direction": direction, "Weight": weight})
        pl.DataFrame(visits).write_csv(path / "Visit.txt", separator="\t")
        pl.DataFrame(pokes).write_csv(path / "Nosepoke.txt", separator="\t")
        if gate:
            (path / "AnimalGate").mkdir()
            pl.DataFrame(gates).write_csv(path / "AnimalGate/Session.txt", separator="\t")
        return path

    return make


@pytest.fixture
def datasets(export_factory):
    return [load_dataset(export_factory(group, lot, extra=i), group=group, lot=lot, phase="phase 1")
            for i, (group, lot) in enumerate([("control", "lot 1"), ("ko", "lot 2")])]


@pytest.fixture
def tables(datasets):
    return build_tables(datasets, DEFAULT_SETTINGS)


@pytest.fixture
def config(datasets):
    return {"run_name": "test run", "output_dir": "results", "groups": ["control", "ko"],
            "datasets": [{"path": str(d.path), "group": d.group, "lot": d.lot, "phase": d.phase} for d in datasets],
            "analyses": [{"table": "mouse_summary", "value": "visits_n", "title": "Visits"},
                         {"table": "corner_summary", "value": "time_s", "by": "corner", "title": "Corners"},
                         {"table": "learning_by_day", "value": "incorrect_pct", "by": "day", "title": "Learning"},
                         {"table": "activity_hourly", "value": "visits_n", "by": "hour", "stats": False,
                          "title": "Activity"},
                         {"table": "gate_sessions", "plot": "gate_raster", "title": "Gate"}]}


def pytest_addoption(parser):
    parser.addoption('--run-browser', action='store_true', default=False, help='run Chrome interaction checks')


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--run-browser'):
        for item in items:
            if 'browser' in item.keywords:
                item.add_marker(pytest.mark.skip(reason='use --run-browser with Chrome installed'))
