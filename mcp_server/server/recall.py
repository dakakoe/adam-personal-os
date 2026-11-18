"""Pure scoring for "who can help with X": build the answer key from circle
memberships and count how many key people a ranking puts in its top k.

No DB and no model, so the evaluation harness and the find_people tool share
one definition of a right answer and one of a forbidden one."""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

KS = (10, 25, 50)


def answer_key(circle_keys: Iterable[str], members: Mapping[str, set[str]],
               excluded: Iterable[str] = ()) -> set[str]:
    """Everyone in any of `circle_keys`, minus anyone in an `excluded` circle —
    even when they are also in a requested one.

    `excluded` is configuration (FIND_PEOPLE_EXCLUDED_CIRCLES), never a literal:
    which circle means "never suggest these people" is personal to the user."""
    wanted = set().union(*(members.get(c, set()) for c in circle_keys))
    barred = set().union(*(members.get(c, set()) for c in excluded))
    return wanted - barred


def hits_at(ranked: Sequence[str], key: set[str], ks: Sequence[int] = KS) -> tuple[int, ...]:
    """How many of `key` appear in the first k of `ranked`, for each k."""
    return tuple(sum(1 for p in ranked[:k] if p in key) for k in ks)


def regressions(
    current: Mapping[str, Sequence[int]], baseline: Mapping[str, Sequence[int]]
) -> list[str]:
    """Arms that found fewer key people than the baseline at any cutoff. An arm
    missing from the baseline is new, and an arm missing now was retired —
    neither is a regression."""
    out = []
    for arm, was in baseline.items():
        now = current.get(arm)
        if now is None:
            continue
        drops = [f"@{k} {w}->{n}" for k, w, n in zip(KS, was, now) if n < w]
        if drops:
            out.append(f"{arm}: " + ", ".join(drops))
    return out
