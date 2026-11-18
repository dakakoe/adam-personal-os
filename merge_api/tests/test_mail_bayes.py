"""Triage iteration — Rspamd Bayes training plumbing.

Pins the pure pieces: /learn* response normalization (200-success / HTTP 208 /
"already learned" error text are all terminal outcomes; anything else raises)
and the batch planner (even split, slack hand-off, cumulative imbalance cap).
"""
from __future__ import annotations

import pytest

from merge_api.rspamd import RspamdError, parse_learn_result, plan_learn_batch


def test_parse_learn_success() -> None:
    assert parse_learn_result(200, {"success": True}) == "learned"


def test_parse_learn_http_208_means_already() -> None:
    assert parse_learn_result(208, None) == "already"


def test_parse_learn_http_204_skip_is_terminal() -> None:
    # 204 = rspamd skipped learning (condition/duplicate) — never retryable
    assert parse_learn_result(204, None) == "already"


def test_parse_learn_already_learned_text() -> None:
    assert parse_learn_result(
        400, {"error": "<abc123> has been already learned as spam, ignore it"}) == "already"


def test_parse_learn_garbage_raises() -> None:
    with pytest.raises(RspamdError):
        parse_learn_result(500, {"error": "internal error"})
    with pytest.raises(RspamdError):
        parse_learn_result(200, {"success": False})


def test_plan_even_split() -> None:
    assert plan_learn_batch(100, 100, limit=50, learned_spam=0, learned_ham=0) == (25, 25)


def test_plan_slack_to_nonexhausted_class_capped() -> None:
    # only 5 spam available → ham gets the leftover budget, but the cumulative
    # 3:1 cap clamps it (learning 45 ham against 5 spam would skew the corpus)
    n_spam, n_ham = plan_learn_batch(5, 500, limit=50, learned_spam=0, learned_ham=0)
    assert n_spam == 5
    assert n_ham == 15


def test_plan_slack_flows_when_balanced() -> None:
    # both classes well-fed already → slack really does go to the open class
    n_spam, n_ham = plan_learn_batch(5, 500, limit=50, learned_spam=400, learned_ham=400)
    assert n_spam == 5
    assert n_ham == 45


def test_plan_zero_available() -> None:
    assert plan_learn_batch(0, 0, limit=50, learned_spam=10, learned_ham=10) == (0, 0)


def test_plan_imbalance_cap() -> None:
    # ham already 3x ahead and no spam available: don't keep piling on ham
    n_spam, n_ham = plan_learn_batch(0, 500, limit=100, learned_spam=50, learned_ham=150)
    assert n_spam == 0
    assert n_ham == 0  # 150/50 is at the 3:1 cap already


def test_plan_cap_allows_catchup() -> None:
    # spam way ahead: no more spam this run; ham learns freely to rebalance
    n_spam, n_ham = plan_learn_batch(500, 500, limit=100, learned_spam=300, learned_ham=0)
    assert n_spam == 0
    assert n_ham == 50
