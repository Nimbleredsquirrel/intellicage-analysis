from __future__ import annotations

import polars as pl

MOUSE_KEYS = ("group", "lot", "animal")


def select_data(df: pl.DataFrame, analysis: dict, groups: list[str], selection: dict | None = None) -> pl.DataFrame:
    """apply the same selection in the app and batch runs."""
    selection = selection or {}
    required = {"group", "lot", "phase", "animal"}
    required.update(analysis.get("filter") or {})
    required.update(analysis[k] for k in ("value", "by") if analysis.get(k))
    if analysis.get('paired_periods'):
        required.update(['day', 'period'])
    if analysis.get('observed_only'):
        required.add('hours')
    if missing := required.difference(df.columns):
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

    df = df.filter(pl.col("group").is_in(selection.get("groups", groups)))
    if selection.get("phases") is not None:
        df = df.filter(pl.col("phase").is_in(selection["phases"]))

    # keep mouse ids separate from their display labels
    excluded = selection.get("exclude_mice") or []
    for mouse in excluded:
        if not isinstance(mouse, dict) or set(mouse) != set(MOUSE_KEYS):
            raise ValueError("Each excluded mouse needs group, lot and animal")
        if not isinstance(mouse["animal"], int) or isinstance(mouse["animal"], bool):
            raise ValueError("Excluded animal ids must be integers")
    if excluded:
        keys = pl.DataFrame(excluded, schema={k: df.schema[k] for k in MOUSE_KEYS})
        df = df.join(keys.unique(), on=list(MOUSE_KEYS), how="anti", maintain_order="left")

    for column, choice in (analysis.get("filter") or {}).items():
        values = choice if isinstance(choice, list) else [choice]
        keep = pl.col(column).is_in([v for v in values if v is not None])
        if None in values:
            keep = keep | pl.col(column).is_null()
        df = df.filter(keep)

    if analysis.get('observed_only'):
        df = df.filter(pl.col('hours') > 0)

    if periods := analysis.get('paired_periods'):
        # pair the night with the date on which it starts
        keys = ['group', 'lot', 'phase', 'animal', 'day']
        df = df.filter(pl.col('period').is_in(periods))
        paired = (df.group_by(keys).agg(pl.col('period').n_unique().alias('windows'))
                  .filter(pl.col('windows') == len(periods)).select(keys))
        df = df.join(paired, on=keys, how='semi', maintain_order='left')

    # figures and tests must see the same finite values
    if value := analysis.get("value"):
        if not df.schema[value].is_numeric():
            raise ValueError(f"Metric '{value}' must be numeric")
        valid = pl.col(value).is_finite()
        if analysis.get("plot") == "latency":
            valid = valid | pl.col(value).is_null()
        df = df.filter(valid)
    if by := analysis.get("by"):
        df = df.drop_nulls(by)
    return df


def present_groups(df: pl.DataFrame, groups: list[str]) -> list[str]:
    """keep the configured order after filtering."""
    present = set(df["group"])
    return [g for g in groups if g in present]
