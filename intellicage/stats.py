from __future__ import annotations

import warnings
from itertools import combinations

import numpy as np
import polars as pl
from scipy import stats as ss
from statsmodels.stats.multitest import multipletests

TWO_GROUP_TESTS = ["welch", "student", "mann_whitney", "auto"]
MULTI_GROUP_TESTS = ["anova", "kruskal"]
REPEATED_TESTS = ["mixed_anova", "lmm"]
POSTHOC_TESTS = ["welch", "student", "mann_whitney"]
PADJUST = ["sidak", "holm", "bonferroni", "fdr_bh"]
PAIRWISE_SCHEMA = {"metric": pl.Utf8, "test": pl.Utf8, "group_a": pl.Utf8, "group_b": pl.Utf8,
                   "diff_b_minus_a": pl.Float64, "statistic": pl.Float64, "df": pl.Float64,
                   "p": pl.Float64, "p_adj": pl.Float64, "summary": pl.Utf8}
OMNIBUS_SCHEMA = {"metric": pl.Utf8, "test": pl.Utf8, "effect": pl.Utf8, "statistic": pl.Float64,
                   "df1": pl.Float64, "df2": pl.Float64, "p": pl.Float64, "summary": pl.Utf8,
                   "sphericity_corrected": pl.Boolean, "gg_epsilon": pl.Float64, "note": pl.Utf8}


def stars(p: float | None) -> str:
    if p is None or not np.isfinite(p):
        return ""
    return "****" if p < 1e-4 else "***" if p < 1e-3 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def fmt_p(p: float | None) -> str:
    if p is None or not np.isfinite(p):
        return "n/a"
    return "P<0.0001" if p < 1e-4 else f"P={p:.4f}"


def _values(df: pl.DataFrame, value: str, group: str) -> np.ndarray:
    v = df.filter(pl.col("group") == group)[value].drop_nulls().cast(pl.Float64).to_numpy()
    return v[np.isfinite(v)]


def _two_group(a: np.ndarray, b: np.ndarray, test: str) -> tuple[str, float, float | None, float]:
    if test == "auto":
        normal = all(len(x) < 3 or (np.ptp(x) > 0 and ss.shapiro(x).pvalue >= 0.05) for x in (a, b))
        test = "welch" if normal else "mann_whitney"
    if test in ("welch", "student"):
        if np.ptp(a) == np.ptp(b) == 0:
            return "Not estimable (no within-group variation)", np.nan, None, np.nan
        r = ss.ttest_ind(b, a, equal_var=test == "student")
        return ("Welch's t test" if test == "welch" else "Unpaired t test"), float(r.statistic), float(r.df), float(r.pvalue)
    if test == "mann_whitney":
        r = ss.mannwhitneyu(a, b, alternative="two-sided")
        return "Mann-Whitney test", float(r.statistic), None, float(r.pvalue)
    raise ValueError(f"unknown two-group test {test!r}")


def adjust_pvalues(values, method: str) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    valid = np.isfinite(values)
    adjusted = np.full(len(values), np.nan)
    if valid.any():
        candidates = np.where(valid, values, 1.)
        with np.errstate(divide="ignore"):
            adjusted[valid] = multipletests(candidates, method=method)[1][valid]
    return adjusted


def validate_observations(df: pl.DataFrame, within: str | None = None) -> None:
    keys = [k for k in ["group", "lot", "phase", "animal"] if k in df.columns]
    if "animal" not in keys:
        return
    if within:
        keys.append(within)
    if df.group_by(keys).len().filter(pl.col("len") > 1).height:
        raise ValueError("More than one observation per mouse. Choose a repeated factor or filter the table first.")


def compare_groups(df: pl.DataFrame, value: str, groups: list[str], test: str = "welch") -> tuple[pl.DataFrame, pl.DataFrame]:
    validate_observations(df)
    desc = (df.filter(pl.col("group").is_in(groups), pl.col(value).is_finite())
            .group_by("group").agg(n=pl.len(), mean=pl.col(value).mean(), sd=pl.col(value).std(),
                                   sem=pl.col(value).std() / pl.len().sqrt(), median=pl.col(value).median())
            .sort(pl.col("group").replace_strict(groups, list(range(len(groups)))))
            .select(pl.lit(value).alias("metric"), pl.all()))
    data = {g: _values(df, value, g) for g in groups}

    # keep the graph available when exclusions leave too few mice
    if len(groups) < 2 or any(len(data[g]) < 2 for g in groups):
        return desc, pl.DataFrame(schema=PAIRWISE_SCHEMA)

    rows = []
    if len(groups) == 2:
        a, b = data[groups[0]], data[groups[1]]
        if len(a) >= 2 and len(b) >= 2:
            effective = {'anova': 'student', 'kruskal': 'mann_whitney'}.get(test, test)
            name, stat, dof, p = _two_group(a, b, effective)
            rows.append({"metric": value, "test": name, "group_a": groups[0], "group_b": groups[1],
                         "diff_b_minus_a": float(np.mean(b) - np.mean(a)), "statistic": stat, "df": dof,
                         "p": p, "p_adj": p, "summary": stars(p)})
    else:
        test = test if test in MULTI_GROUP_TESTS else "kruskal" if test == "mann_whitney" else "anova"
        xs = [data[g] for g in groups if len(data[g]) >= 2]
        pairs = list(combinations(range(len(groups)), 2))
        if np.ptp(np.concatenate(xs)) == 0 or (test == 'anova' and all(np.ptp(x) == 0 for x in xs)):
            unavailable = {"metric": value, "test": "Not estimable (no within-group variation)",
                           "group_a": "all", "group_b": "all", "summary": ""}
            return desc, pl.DataFrame([unavailable], schema=PAIRWISE_SCHEMA)
        if test == "anova":
            omni, name = ss.f_oneway(*xs), "One-way ANOVA + Tukey"
            tk = ss.tukey_hsd(*xs)
            raw = adj = [tk.pvalue[i, j] for i, j in pairs]
        else:
            omni, name = ss.kruskal(*xs), "Kruskal-Wallis + Mann-Whitney (Holm)"
            raw = [ss.mannwhitneyu(data[groups[i]], data[groups[j]]).pvalue for i, j in pairs]
            adj = adjust_pvalues(raw, "holm")
        rows.append({"metric": value, "test": name, "group_a": "all", "group_b": "all", "diff_b_minus_a": None,
                     "statistic": float(omni.statistic), "df": None, "p": float(omni.pvalue),
                     "p_adj": float(omni.pvalue), "summary": stars(omni.pvalue)})
        for (i, j), p, pa in zip(pairs, raw, adj):
            rows.append({"metric": value, "test": name, "group_a": groups[i], "group_b": groups[j],
                         "diff_b_minus_a": float(np.mean(data[groups[j]]) - np.mean(data[groups[i]])),
                         "statistic": None, "df": None, "p": float(p), "p_adj": float(pa), "summary": stars(pa)})
    return desc, pl.DataFrame(rows, schema=PAIRWISE_SCHEMA)


def repeated_measures(df: pl.DataFrame, value: str, within: str, groups: list[str], test: str = "mixed_anova",
                      posthoc: str = "welch", padjust: str = "sidak") -> tuple[pl.DataFrame, pl.DataFrame]:
    if test not in REPEATED_TESTS:
        raise ValueError(f"unknown repeated-measures test {test!r}")
    validate_observations(df, within)
    d = (df.filter(pl.col("group").is_in(groups), pl.col(value).is_finite())
         .select(subject=pl.concat_str("group", "lot", pl.col("animal").cast(pl.Utf8), separator="|"),
                 group="group", w=within, y=value)
         .drop_nulls())
    levels = [str(x) for x in d["w"].unique().sort()]
    d = d.with_columns(pl.col("w").cast(pl.Utf8))

    def unavailable(note):
        return pl.DataFrame([{"metric": value, "test": test, "effect": "model", "note": note}], schema=OMNIBUS_SCHEMA)

    omnibus = None
    if len(levels) < 2 or len(groups) < 2:
        omnibus = unavailable("At least two groups and two repeated levels are required")
    elif test == "mixed_anova":
        import pingouin as pg

        n_levels = d.group_by("subject").agg(pl.col("w").n_unique())
        dropped = sorted(n_levels.filter(pl.col("w") < len(levels))["subject"])
        complete = d.filter(~pl.col("subject").is_in(dropped))
        sizes = complete.group_by("group").agg(pl.col("subject").n_unique().alias("n"))
        if sizes.height < len(groups) or sizes["n"].min() < 2:
            omnibus = unavailable("Fewer than two complete mice per group; use LMM or change the selection")
        else:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                aov = pg.mixed_anova(data=complete.to_pandas(), dv="y", within="w",
                                 between="group", subject="subject", correction=True)
            aov = pl.from_pandas(aov)
            missing = [c for c in ('F', 'p_unc', 'eps') if c not in aov.columns]
            if missing:
                aov = aov.with_columns(*[pl.lit(None, pl.Float64).alias(c) for c in missing])
            gg = pl.col("p_GG_corr") if "p_GG_corr" in aov.columns else pl.lit(None, pl.Float64)
            note = "; ".join(([f"dropped (incomplete): {', '.join(dropped)}"] if dropped else []) +
                             sorted({str(w.message) for w in caught}))
            omnibus = aov.select(
                metric=pl.lit(value), test=pl.lit("Two-way mixed ANOVA"),
                effect=pl.col("Source").replace("w", within), statistic="F", df1="DF1", df2="DF2",
                p=pl.coalesce(gg.fill_nan(None), "p_unc"),
                sphericity_corrected=gg.is_not_null() & gg.is_not_nan(), gg_epsilon="eps", note=pl.lit(note),
            ).with_columns(pl.col("statistic", "df1", "df2").cast(pl.Float64))
            means = complete.group_by("subject").agg(pl.col("y").mean())["y"].to_numpy()
            if len(means) and np.ptp(means) < 1e-10 * max(1., np.abs(means).max()):
                # percentages that sum to 100 cannot have a group main effect
                omnibus = omnibus.with_columns(
                    pl.when(pl.col("effect") == "group").then(None).otherwise(pl.col("p")).alias("p"),
                    pl.when(pl.col("effect") == "group").then(None).otherwise(pl.col("statistic")).alias("statistic"),
                    pl.when(pl.col("effect") == "group").then(pl.lit("Not estimable: every mouse has the same mean across levels"))
                    .otherwise(pl.col("note")).alias("note"))
    elif test == "lmm":
        import statsmodels.formula.api as smf

        pdf = d.to_pandas()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fit = smf.mixedlm("y ~ C(group, Sum) * C(w, Sum)", pdf, groups=pdf["subject"]).fit(
                reml=True, method=["lbfgs", "powell"])
            wald = pl.from_pandas(fit.wald_test_terms(scalar=True).summary_frame(), include_index=True)
        note = "; ".join(sorted({str(w.message) for w in caught}))
        if not fit.converged:
            note = "Model did not converge; P values are unavailable. " + note
        omnibus = wald.rename({wald.columns[0]: "term"}).filter(pl.col("term") != "Intercept").select(
            metric=pl.lit(value), test=pl.lit("LMM (REML, sum contrasts), Wald chi2"),
            effect=pl.col("term").str.replace_all("C(group, Sum)", "group", literal=True)
            .str.replace_all("C(w, Sum)", within, literal=True).str.replace_all(":", " * ", literal=True),
            statistic=pl.col("chi2").cast(pl.Float64), df1=pl.col("df constraint").cast(pl.Float64),
            df2=pl.lit(None, pl.Float64), p=pl.col("P>chi2").cast(pl.Float64) if fit.converged else pl.lit(None, pl.Float64),
            sphericity_corrected=pl.lit(False), gg_epsilon=pl.lit(None, pl.Float64), note=pl.lit(note),
        )
    omnibus = omnibus.with_columns(summary=pl.col("p").map_elements(stars, return_dtype=pl.Utf8)).select(
        "metric", "test", "effect", "statistic", "df1", "df2", "p", "summary", "sphericity_corrected", "gg_epsilon",
        "note")

    ph = []
    for lvl in levels:
        sub = d.filter(pl.col("w") == lvl).rename({"y": value})
        for ga, gb in combinations(groups, 2):
            a, b = _values(sub, value, ga), _values(sub, value, gb)
            if len(a) < 2 or len(b) < 2:
                continue
            name, stat, dof, p = _two_group(a, b, posthoc)
            ph.append({"metric": value, "level": lvl, "test": name, "group_a": ga, "group_b": gb,
                       "mean_a": float(a.mean()), "mean_b": float(b.mean()), "statistic": stat, "df": dof, "p": p})
    posthoc_df = pl.DataFrame(ph, schema_overrides={"df": pl.Float64})
    if posthoc_df.height:
        posthoc_df = posthoc_df.with_columns(
            p_adj=pl.Series(adjust_pvalues(posthoc_df["p"].to_numpy(), padjust)),
            correction=pl.lit(padjust),
        ).with_columns(summary=pl.col("p_adj").map_elements(stars, return_dtype=pl.Utf8))
    return omnibus, posthoc_df


def compare_survival(df: pl.DataFrame, value: str, groups: list[str], padjust: str = "sidak") -> tuple[pl.DataFrame, pl.DataFrame]:
    """retain mice without an observed visit as right-censored."""
    validate_observations(df)
    desc, rows = [], []
    data = {}
    for group in groups:
        sub = df.filter(group=group)
        observed = sub[value].drop_nulls().to_numpy()
        censored = sub.filter(pl.col(value).is_null())["window_s"].to_numpy()
        data[group] = ss.CensoredData(uncensored=observed, right=censored)
        desc.append({"metric": value, "group": group, "n": sub.height, "observed": len(observed),
                     "censored": len(censored), "median_observed_s": float(np.median(observed)) if len(observed) else None})
    for ga, gb in combinations(groups, 2):
        a, b = data[ga], data[gb]
        if len(a) < 2 or len(b) < 2:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result = ss.logrank(a, b)
        rows.append({"metric": value, "test": "Log-rank (right-censored), chi2", "group_a": ga, "group_b": gb,
                     "statistic": float(result.statistic ** 2), "df": 1., "p": float(result.pvalue)})
    tests = pl.DataFrame(rows, schema=PAIRWISE_SCHEMA)
    if tests.height:
        tests = tests.with_columns(p_adj=pl.Series(adjust_pvalues(tests["p"], padjust))).with_columns(
            summary=pl.col("p_adj").map_elements(stars, return_dtype=pl.Utf8))
    return pl.DataFrame(desc), tests


def period_comparisons(df: pl.DataFrame, value: str, groups: list[str], periods: list[str], *,
                       paired_test: str = "paired_t", posthoc: str = "welch", padjust: str = "sidak") -> pl.DataFrame:
    """pair each mouse's day and night before comparing them."""
    rows = []
    df = df.filter(pl.col(value).is_finite())
    for day in sorted(df["day"].unique()):
        daily = df.filter(day=day)
        for group in groups:
            sub = daily.filter(group=group)
            for pa, pb in combinations(periods, 2):
                a = sub.filter(period=pa).select("lot", "animal", value)
                b = sub.filter(period=pb).select("lot", "animal", value)
                pair = a.join(b, on=["lot", "animal"], suffix="_b")
                if pair.height < 2:
                    continue
                va, vb = pair[value].to_numpy(), pair[f"{value}_b"].to_numpy()
                if paired_test == "wilcoxon":
                    stat, p = (0., 1.) if np.all(va == vb) else ss.wilcoxon(va, vb)
                elif paired_test == "paired_t":
                    stat, p = (np.nan, np.nan) if np.ptp(vb - va) == 0 else ss.ttest_rel(vb, va)
                else:
                    raise ValueError(f"Unknown paired test: {paired_test}")
                rows.append({"metric": value, "day": day, "period": f"{pa} vs {pb}", "group_a": group, "group_b": group,
                             "contrast": "within mouse", "test": paired_test, "n": pair.height,
                             "statistic": float(stat), "p": float(p)})
        for period in periods:
            sub = daily.filter(period=period)
            for ga, gb in combinations(groups, 2):
                a, b = _values(sub, value, ga), _values(sub, value, gb)
                if len(a) < 2 or len(b) < 2:
                    continue
                name, stat, _, p = _two_group(a, b, posthoc)
                rows.append({"metric": value, "day": day, "period": period, "group_a": ga, "group_b": gb,
                             "contrast": "between groups", "test": name, "n": len(a) + len(b), "statistic": stat, "p": p})
    schema = {"metric": pl.Utf8, "day": pl.Int64, "period": pl.Utf8, "group_a": pl.Utf8, "group_b": pl.Utf8,
              "contrast": pl.Utf8, "test": pl.Utf8, "n": pl.Int64, "statistic": pl.Float64, "p": pl.Float64}
    result = pl.DataFrame(rows, schema=schema)
    if result.height:
        result = result.with_columns(p_adj=pl.Series(adjust_pvalues(result["p"], padjust)), correction=pl.lit(padjust))
        result = result.with_columns(summary=pl.col("p_adj").map_elements(stars, return_dtype=pl.Utf8))
    return result
