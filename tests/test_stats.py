import math

import numpy as np
import polars as pl
import pytest

from intellicage import stats as S


@pytest.mark.parametrize("p,text", [(None, ""), (math.nan, ""), (math.inf, ""), (.05, "ns"),
                                    (.049, "*"), (.009, "**"), (.0009, "***"), (.00009, "****")])
def test_significance_boundaries(p, text):
    assert S.stars(p) == text


@pytest.mark.parametrize("p,text", [(None, "n/a"), (math.nan, "n/a"), (.00001, "P<0.0001"),
                                    (.012345, "P=0.0123")])
def test_displayed_p_values(p, text):
    assert S.fmt_p(p) == text


@pytest.mark.parametrize("test", ["welch", "student", "auto"])
def test_t_test_known_answer_and_descriptive_sample_size(test):
    df = pl.DataFrame({"group": ["a"] * 3 + ["b"] * 3, "y": [1., 2., 3., 2., 3., 4.]})
    desc, result = S.compare_groups(df, "y", ["a", "b"], test)
    row = result.row(0, named=True)
    assert desc["n"].to_list() == [3, 3]
    assert desc["mean"].to_list() == [2., 3.]
    assert desc["sd"].to_list() == [1., 1.]
    assert row["statistic"] == pytest.approx(math.sqrt(1.5))
    assert row["df"] == 4
    assert row["p"] == pytest.approx(.2878641347266908)
    assert row["diff_b_minus_a"] == 1


def test_mann_whitney_exact_small_sample_answer():
    df = pl.DataFrame({"group": ["a"] * 3 + ["b"] * 3, "y": [1., 2., 3., 4., 5., 6.]})
    _, result = S.compare_groups(df, "y", ["a", "b"], "mann_whitney")
    assert result["statistic"][0] == 0
    assert result["p"][0] == pytest.approx(.1)
    assert result["df"][0] is None


def test_auto_detects_strong_nonnormality():
    df = pl.DataFrame({"group": ["a"] * 8 + ["b"] * 8,
                       "y": [0.] * 7 + [100.] + [float(i) for i in range(8)]})
    _, result = S.compare_groups(df, "y", ["a", "b"], "auto")
    assert result["test"][0] == "Mann-Whitney test"


@pytest.mark.parametrize("test", ["anova", "kruskal", "mann_whitney"])
def test_three_groups_have_one_omnibus_and_three_named_pairs(test):
    df = pl.DataFrame({"group": ["a"] * 3 + ["b"] * 3 + ["c"] * 3,
                       "y": [1., 2., 3., 2., 3., 4., 3., 4., 5.]})
    _, result = S.compare_groups(df, "y", ["a", "b", "c"], test)
    assert result.select("group_a", "group_b").rows() == [("all", "all"), ("a", "b"), ("a", "c"), ("b", "c")]
    assert result["p_adj"].is_between(0, 1).all()
    if test == "anova":
        assert result["statistic"][0] == pytest.approx(3.)
        assert result["p"][0] == pytest.approx(.125)
    else:
        assert "Holm" in result["test"][0]


@pytest.mark.parametrize("groups,values", [(["a", "b"], [1., 2.]),
                                           (["a", "a", "b", "b", "c"], [1., 2., 2., 3., 4.]),
                                           (["a", "a"], [1., 2.])])
def test_small_samples_return_typed_empty_tests(groups, values):
    df = pl.DataFrame({"group": groups, "y": values})
    desc, result = S.compare_groups(df, "y", list(dict.fromkeys(groups)))
    assert desc["n"].sum() == len(values)
    assert result.is_empty()
    assert result.schema == S.PAIRWISE_SCHEMA


def test_nonfinite_values_are_not_counted_as_mice():
    df = pl.DataFrame({"group": ["a"] * 5 + ["b"] * 2, "y": [1., 2., None, math.nan, math.inf, 2., 3.]})
    desc, result = S.compare_groups(df, "y", ["a", "b"])
    assert desc["n"].to_list() == [2, 2]
    assert math.isfinite(result["p"][0])


def test_unknown_independent_test_is_rejected():
    df = pl.DataFrame({"group": ["a", "a", "b", "b"], "y": [1., 2., 2., 3.]})
    with pytest.raises(ValueError, match="unknown two-group test"):
        S.compare_groups(df, "y", ["a", "b"], "typo")


def test_mixed_anova_degrees_of_freedom_and_sidak_family(tables):
    omni, posthoc = S.repeated_measures(tables["corner_summary"], "time_s", "corner", ["control", "ko"])
    group = omni.filter(effect="group")
    within = omni.filter(effect="corner")
    assert group["df1"][0] == 1
    assert group["df2"][0] == 4
    assert within["df1"][0] == 3
    assert within["df2"][0] == 12
    assert within["sphericity_corrected"][0]
    valid = posthoc.filter(pl.col("p").is_finite())
    np.testing.assert_allclose(valid["p_adj"], -np.expm1(len(posthoc) * np.log1p(-valid["p"].to_numpy())))


def test_incomplete_mouse_is_reported(tables):
    data = tables["corner_summary"].filter(~((pl.col("group") == "control") &
                                             (pl.col("animal") == 1) & (pl.col("corner") == 1)))
    omni, _ = S.repeated_measures(data, "time_s", "corner", ["control", "ko"])
    assert all("control|lot 1|1" in note for note in omni["note"])


def test_lmm_handles_missing_measurement():
    rng = np.random.default_rng(42)
    rows = []
    for gi, group in enumerate(["control", "ko"]):
        for animal in range(8):
            intercept = rng.normal(0, 2)
            for day in range(1, 5):
                if (gi, animal, day) == (0, 0, 4):
                    continue
                rows.append({"group": group, "lot": str(gi), "animal": animal, "day": day,
                             "y": 10 + intercept + gi * 2 + day + rng.normal()})
    omni, posthoc = S.repeated_measures(pl.DataFrame(rows), "y", "day", ["control", "ko"], test="lmm")
    assert set(omni["effect"]) == {"group", "day", "group * day"}
    assert omni["p"].is_between(0, 1).all()
    assert posthoc.height == 4


def test_unknown_repeated_test_is_rejected(tables):
    with pytest.raises(ValueError, match="unknown repeated-measures test"):
        S.repeated_measures(tables["corner_summary"], "time_s", "corner", ["control", "ko"], test="typo")


def test_duplicate_mouse_values_are_rejected(tables):
    data=pl.concat([tables['mouse_summary'],tables['mouse_summary'].head(1)])
    with pytest.raises(ValueError,match='More than one observation'):
        S.compare_groups(data,'visits_n',['control','ko'])


def test_nonestimable_comparisons_do_not_corrupt_adjustment():
    adjusted=S.adjust_pvalues([.01, np.nan, .03],'fdr_bh')
    assert adjusted[0] == pytest.approx(.03)
    assert np.isnan(adjusted[1])
    assert adjusted[2] == pytest.approx(.045)
    assert np.isnan(S.adjust_pvalues([np.nan],'sidak')[0])
    _,tests=S.compare_groups(pl.DataFrame({'group':['a','a','b','b'],'y':[1.,1.,1.,1.]}),'y',['a','b'])
    assert tests['test'][0].startswith('Not estimable')
    assert not np.isfinite(tests['p'][0])


def test_repeated_model_reports_insufficient_complete_mice(tables):
    data=tables['corner_summary'].filter(pl.col('animal')==1)
    omni,posthoc=S.repeated_measures(data,'time_s','corner',['control','ko'])
    assert 'Fewer than two' in omni['note'][0]
    assert posthoc.is_empty()
    omni,_=S.repeated_measures(data.filter(corner=1),'time_s','corner',['control','ko'])
    assert 'two repeated levels' in omni['note'][0]


def test_corner_percentages_cannot_have_a_group_mean_effect(tables):
    omni,_=S.repeated_measures(tables['corner_summary'],'time_pct','corner',['control','ko'])
    assert omni.filter(effect='group')['p'][0] is None
    assert 'same mean' in omni.filter(effect='group')['note'][0]


def test_latency_nonvisitors_are_included_in_logrank():
    df=pl.DataFrame({'group':['a']*3+['b']*3, 'lot':['x']*6, 'animal':[1,2,3,1,2,3],
                     'latency':[1.,2.,None,4.,None,None], 'window_s':[5.]*6})
    desc,test=S.compare_survival(df,'latency',['a','b'])
    assert desc['n'].to_list()==[3,3]
    assert desc['censored'].to_list()==[1,2]
    assert 0 < test['p'][0] < 1
    desc,test=S.compare_survival(df.filter(animal=1),'latency',['a','b'])
    assert test.is_empty()
    desc,_=S.compare_survival(df.with_columns(pl.lit(None,pl.Float64).alias('latency')),'latency',['a','b'])
    assert desc['median_observed_s'].null_count()==2


def test_day_night_uses_paired_mice_and_distinguishes_between_group_tests(tables):
    data=tables['activity_periods']
    paired=S.period_comparisons(data,'visits_n',['control','ko'],['Light','Dark'],paired_test='wilcoxon')
    assert set(paired['contrast'])=={'within mouse','between groups'}
    assert paired['p_adj'].drop_nans().is_between(0,1).all()
    with pytest.raises(ValueError,match='Unknown paired test'):
        S.period_comparisons(data,'visits_n',['control','ko'],['Light','Dark'],paired_test='typo')
    empty=S.period_comparisons(data.filter(animal=1),'visits_n',['control','ko'],['Light','Dark'])
    assert empty.is_empty()


@pytest.mark.parametrize('method',['anova','kruskal'])
def test_multigroup_method_still_works_after_selecting_two_groups(method):
    df=pl.DataFrame({'group':['a']*3+['b']*3,'y':[1.,2.,3.,2.,3.,4.]})
    _,tests=S.compare_groups(df,'y',['a','b'],method)
    assert 0 < tests['p'][0] < 1


@pytest.mark.parametrize('method',['anova','kruskal'])
def test_three_constant_groups_return_an_explanation(method):
    df=pl.DataFrame({'group':['a']*3+['b']*3+['c']*3,'y':[0.]*9})
    _,tests=S.compare_groups(df,'y',['a','b','c'],method)
    assert tests['p'][0] is None
    assert tests['test'][0].startswith('Not estimable')
