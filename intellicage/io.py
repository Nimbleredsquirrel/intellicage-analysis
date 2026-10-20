from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import polars as pl

TS_FMT = "%Y-%m-%d %H:%M:%S%.3f"


def _read_tsv(path: Path, ints=(), floats=(), required=()) -> pl.DataFrame:
    df = pl.read_csv(path, separator="\t", infer_schema_length=0, encoding="utf8-lossy")
    if missing := set(required).difference(df.columns):
        raise ValueError(f"{path.name}: missing columns: {', '.join(sorted(missing))}")
    return df.with_columns(
        *[pl.col(c).cast(pl.Int64, strict=False) for c in ints if c in df.columns],
        *[pl.col(c).cast(pl.Float64, strict=False) for c in floats if c in df.columns],
    )


def _ts(*cols: str) -> pl.Expr:
    return pl.concat_str(cols, separator=" ").str.to_datetime(TS_FMT, strict=False).dt.cast_time_unit("us")


@dataclass
class Dataset:
    path: Path
    group: str
    lot: str
    phase: str
    start: datetime
    end: datetime
    animals: pl.DataFrame
    visits: pl.DataFrame
    nosepokes: pl.DataFrame
    gate_sessions: pl.DataFrame | None
    learning: bool
    sessions: pl.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)

    def tag(self, df: pl.DataFrame) -> pl.DataFrame:
        return df.select(pl.lit(self.group).alias("group"), pl.lit(self.lot).alias("lot"),
                         pl.lit(self.phase).alias("phase"), pl.all())


def load_dataset(path: str | Path, *, group: str, lot: str = "", phase: str = "", learning: bool | None = None,
                 exclude_animals: list[int] | None = None) -> Dataset:
    path = Path(path).expanduser()
    if not (path / "Visit.txt").exists():
        raise FileNotFoundError(f"{path} does not look like an IntelliCage export (no Visit.txt)")

    sessions = _read_tsv(path / "Sessions.txt", required=["SessionStart", "SessionEnd"]).with_columns(
        start=pl.col("SessionStart").str.to_datetime(strict=False),
        end=pl.col("SessionEnd").str.to_datetime(strict=False))
    animals = _read_tsv(path / "Animal.txt", ints=["Animal"], required=["Animal"])
    visits = _read_tsv(
        path / "Visit.txt", required=["StartDate", "StartTime", "EndDate", "EndTime", "VisitID"],
        ints=["VisitID", "Animal", "Cage", "Corner", "PlaceError", "SideErrors", "NosepokeNumber", "LickNumber",
              "Session"],
        floats=["NosepokeDuration", "LickDuration", "LickContactTime", "VisitDuration"],
    ).with_columns(start=_ts("StartDate", "StartTime"), end=_ts("EndDate", "EndTime"))
    nosepokes = _read_tsv(
        path / "Nosepoke.txt", required=["StartDate", "StartTime", "EndDate", "EndTime"],
        ints=["VisitID", "NosepokeOrder", "Animal", "Cage", "Corner", "Side", "SideError", "LickNumber"],
        floats=["LickDuration", "LickContactTime", "NosepokeDuration"],
    ).with_columns(start=_ts("StartDate", "StartTime"), end=_ts("EndDate", "EndTime"))
    gate_file = path / "AnimalGate" / "Session.txt"
    gate = _read_tsv(gate_file, ints=["SessionId", "Animal", "Address"], floats=["Weight"],
                     required=["Start", "End", "Animal", "Direction", "Weight"]).with_columns(
        _ts("Start"), _ts("End")) if gate_file.exists() else None

    # validate the fields used by every calculation
    required = {
        "visits": (visits, ["Animal", "Corner", "VisitDuration", "NosepokeNumber", "LickNumber", "LickDuration",
                              "LickContactTime", "start", "end"]),
        "nosepokes": (nosepokes, ["Animal", "VisitID", "NosepokeDuration", "LickNumber", "LickDuration",
                                    "SideCondition", "start", "end"]),
        "sessions": (sessions, ["start", "end"]),
    }
    for name, (frame, columns) in required.items():
        missing = set(columns).difference(frame.columns)
        if missing:
            raise ValueError(f"{path.name}: {name} missing columns: {', '.join(sorted(missing))}")
        for column in columns:
            if frame[column].null_count():
                raise ValueError(f"{path.name}: {name}.{column} contains missing or invalid values")
        for column, dtype in frame.schema.items():
            if dtype.is_float() and not frame[column].drop_nulls().is_finite().all():
                raise ValueError(f"{path.name}: {name}.{column} contains non-finite values")
        for column in columns:
            if frame.schema[column].is_numeric() and any(word in column for word in ('Duration', 'Number', 'ContactTime')):
                if frame.filter(pl.col(column) < 0).height:
                    raise ValueError(f"{path.name}: {name}.{column} contains negative values")
        if frame.filter(pl.col("end") < pl.col("start")).height:
            raise ValueError(f"{path.name}: {name} contains negative time intervals")
    if sessions.is_empty() or sessions["end"].max() <= sessions["start"].min():
        raise ValueError(f"{path.name}: no positive recording interval")
    if visits.filter(~pl.col("Corner").is_in([1, 2, 3, 4])).height:
        raise ValueError(f"{path.name}: corner ids must be 1, 2, 3 or 4")
    if animals['Animal'].null_count() or animals['Animal'].n_unique() != animals.height:
        raise ValueError(f'{path.name}: Animal.txt must contain unique, valid animal ids')
    if visits['VisitID'].n_unique() != visits.height:
        raise ValueError(f'{path.name}: duplicate visit ids')
    if nosepokes.filter(~pl.col('VisitID').is_in(visits['VisitID'].to_list())).height:
        raise ValueError(f'{path.name}: nosepokes refer to missing visits')
    if gate is not None:
        if any(gate[c].null_count() for c in ['Start', 'End', 'Animal', 'Direction']):
            raise ValueError(f'{path.name}: invalid AnimalGate values')
        if gate.filter(pl.col('End') < pl.col('Start')).height:
            raise ValueError(f'{path.name}: negative AnimalGate time intervals')
    known = animals["Animal"].to_list()
    if visits.filter(~pl.col("Animal").is_in(known)).height or nosepokes.filter(~pl.col("Animal").is_in(known)).height:
        raise ValueError(f"{path.name}: events refer to animals missing from Animal.txt")

    if exclude_animals:
        keep = ~pl.col("Animal").is_in(exclude_animals)
        animals, visits, nosepokes = animals.filter(keep), visits.filter(keep), nosepokes.filter(keep)
        gate = gate.filter(keep) if gate is not None else None

    if learning is None:
        learning = nosepokes["SideCondition"].is_in(["Correct", "Incorrect"]).any()

    notes = []
    silent = animals.filter(~pl.col("Animal").is_in(visits["Animal"].unique().to_list()))["Animal"].to_list()
    if silent:
        notes.append(f"No visits recorded for animals: {', '.join(map(str, silent))}")
    if visits["NosepokeNumber"].sum() != nosepokes.height:
        notes.append("Visit nosepoke totals differ from Nosepoke.txt; check the export filters")
    if visits["LickNumber"].sum() != nosepokes["LickNumber"].sum():
        notes.append("Visit lick totals differ from Nosepoke.txt; check the export filters")
    return Dataset(
        path=path, group=group, lot=lot, phase=phase,
        start=sessions["start"].min(), end=sessions["end"].max(),
        animals=animals, visits=visits, nosepokes=nosepokes, gate_sessions=gate, learning=bool(learning),
        sessions=sessions, warnings=notes,
    )
