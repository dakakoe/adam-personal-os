"""compose_bio_summary — the profile for someone you've never messaged.

Deliberately not an LLM call: ~2,500 people have nothing but a headline and a
company, and paraphrasing "VP Sales at Acme" costs money and loses fidelity.
These pin the shape and, more importantly, the refusal to write anything when
there is nothing to say.

Run on the droplet: this package depends on torch, so `uv run` here would pull
a multi-gigabyte install for four assertions.
"""

from __future__ import annotations

from profile_builder.main import compose_bio_summary


def _bio(text):
    return {"source": "linkedin", "kind": "role", "text": text}


def test_composes_name_and_role():
    assert compose_bio_summary("Dana Lee", [_bio("VP Sales · Acme")]) == "Dana Lee. VP Sales · Acme."


def test_includes_location_when_known():
    got = compose_bio_summary("Dana Lee", [_bio("VP Sales")], "Berlin")
    assert "Berlin" in got and got.startswith("Dana Lee.")


def test_several_sources_are_joined():
    got = compose_bio_summary("Dana Lee", [_bio("VP Sales"), _bio("Ex-Google")])
    assert "VP Sales" in got and "Ex-Google" in got


def test_duplicates_collapse():
    # The same role often arrives from LinkedIn evidence AND an imported
    # contact; repeating it would skew the embedding toward that phrase.
    got = compose_bio_summary("Dana Lee", [_bio("VP Sales"), _bio("vp sales")])
    assert got.lower().count("vp sales") == 1


def test_nothing_to_say_returns_none():
    # A bare name must never get an embedding — it would match every query
    # weakly and pollute semantic search with noise.
    assert compose_bio_summary("Dana Lee", []) is None
    assert compose_bio_summary("Dana Lee", None) is None
    assert compose_bio_summary("Dana Lee", [_bio(""), _bio("   "), _bio(None)]) is None


def test_location_alone_is_enough():
    got = compose_bio_summary("Dana Lee", [], "Lisbon")
    assert got is not None and "Lisbon" in got
