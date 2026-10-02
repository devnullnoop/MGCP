"""Checks on the LoCoMo measurement tools themselves.

A benchmark is an instrument. An instrument nobody checked produces confident
wrong numbers, which is worse than no number. The AUC estimator here is checked
against a brute force count over every pair, because the fast version uses an
identity about ranks that is easy to get subtly wrong when values tie.
"""

import random

import pytest

from tests.locomo_benchmark import ANSWERABLE, auc, mcnemar_exact


def auc_brute(pos, neg):
    """Every pair counted directly. Slow, and obviously correct."""
    total = 0.0
    for p in pos:
        for n in neg:
            total += 1.0 if p > n else (0.5 if p == n else 0.0)
    return total / (len(pos) * len(neg))


class TestAuc:
    @pytest.mark.parametrize(
        "pos,neg,expected",
        [
            ([3, 4, 5], [0, 1, 2], 1.0),
            ([0, 1, 2], [3, 4, 5], 0.0),
            ([1, 1, 1], [1, 1, 1], 0.5),
            ([2, 4], [1, 3], 0.75),
            # Four pairs: 2>1, 2==2, 3>1, 3>2 gives 1 + 0.5 + 1 + 1 over 4.
            ([2, 3], [1, 2], 0.875),
        ],
    )
    def test_known_cases(self, pos, neg, expected):
        assert auc(pos, neg) == pytest.approx(expected)

    def test_matches_brute_force_with_heavy_ties(self):
        """The tie handling is the part that breaks, so ties are made common."""
        rng = random.Random(3)
        for _ in range(300):
            pos = [rng.randint(0, 5) for _ in range(rng.randint(1, 25))]
            neg = [rng.randint(0, 5) for _ in range(rng.randint(1, 25))]
            assert auc(pos, neg) == pytest.approx(auc_brute(pos, neg), abs=1e-12)

    def test_matches_brute_force_on_continuous_values(self):
        rng = random.Random(4)
        for _ in range(100):
            pos = [rng.random() for _ in range(rng.randint(1, 40))]
            neg = [rng.random() for _ in range(rng.randint(1, 40))]
            assert auc(pos, neg) == pytest.approx(auc_brute(pos, neg), abs=1e-12)

    def test_an_empty_group_is_not_a_number(self):
        assert auc([], [1.0]) != auc([], [1.0])  # nan is not equal to itself


class TestMcnemar:
    """The paired test behind every "too close to call" verdict in the report."""

    def test_no_disagreement_means_no_evidence(self):
        assert mcnemar_exact(0, 0) == 1.0

    def test_a_balanced_split_is_not_significant(self):
        assert mcnemar_exact(50, 50) == pytest.approx(1.0)

    def test_a_lopsided_split_is_significant(self):
        assert mcnemar_exact(40, 10) < 0.001

    def test_it_is_two_sided(self):
        """Direction must not change the p-value."""
        assert mcnemar_exact(40, 10) == pytest.approx(mcnemar_exact(10, 40))

    def test_it_matches_a_hand_computable_case(self):
        """b=1, c=4. Two sided exact binomial on 5 trials.

        P(X<=1) = (1 + 5) / 32 = 0.1875, doubled is 0.375.
        """
        assert mcnemar_exact(1, 4) == pytest.approx(0.375)

    def test_the_observed_margin_from_the_report(self):
        """71 against 55 is the mgcp/dragon split, and it is not significant."""
        assert mcnemar_exact(71, 55) > 0.05


def test_answerable_categories_exclude_the_trick_questions():
    """Category 5 is the one the conversation does not answer."""
    assert 5 not in ANSWERABLE
    assert set(ANSWERABLE) == {1, 2, 3, 4}
