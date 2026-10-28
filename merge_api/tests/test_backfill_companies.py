"""normalize_company_name — the gate that decides what becomes a company.

Pure and cheap to pin here; the rest of the backfill is SQL. The risk it
guards is junk employer text ("Freelance", "-", "Self-employed") turning into
real company entities that then clutter the company filter and search.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load the script module directly (scripts/ isn't a package).
_spec = importlib.util.spec_from_file_location(
    "backfill_companies",
    Path(__file__).resolve().parents[2] / "scripts" / "backfill_companies.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
normalize = _mod.normalize_company_name


class TestNormalizeCompanyName:
    @pytest.mark.parametrize("raw,expected", [
        ("Acme, Inc.", "Acme, Inc."),
        ("  EQ   LAB ", "EQ LAB"),                 # whitespace collapsed
        ("Crypto Finance Conference", "Crypto Finance Conference"),
        ("Bureau 49", "Bureau 49"),
    ])
    def test_real_companies_pass_through(self, raw, expected):
        assert normalize(raw) == expected

    @pytest.mark.parametrize("raw", [
        None, "", "  ", "-", "—", "N/A", "n/a", "None",
        "Freelance", "freelancer", "Self-employed", "self employed",
        "Independent", "Unemployed", "Student", "Retired", "Stealth Startup",
        "X",                                       # single char
    ])
    def test_junk_is_dropped(self, raw):
        assert normalize(raw) is None

    def test_case_insensitive_junk(self):
        assert normalize("FREELANCE") is None
        assert normalize("Stealth Mode") is None
