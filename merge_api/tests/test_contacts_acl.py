"""Guards the budget (member) API allow-list for the contacts slice: a member may
READ the contacts list/detail but must NOT reach the person sub-routes (identities,
drafts, merge, photo, sharing). A regression here would over-expose the CRM."""

from __future__ import annotations

from merge_api.app import _budget_api_allowed

PID = "11111111-1111-1111-1111-111111111111"


def test_member_can_read_contacts_list_and_detail():
    assert _budget_api_allowed("GET", "/api/persons") is True
    assert _budget_api_allowed("GET", "/api/persons/count") is True
    assert _budget_api_allowed("GET", f"/api/persons/{PID}") is True


def test_member_cannot_reach_person_subroutes():
    for path in (
        f"/api/persons/{PID}/identities",
        f"/api/persons/{PID}/drafts",
        f"/api/persons/{PID}/similar",
        f"/api/persons/{PID}/photo",
        f"/api/persons/{PID}/sharing",      # owner-only
    ):
        assert _budget_api_allowed("GET", path) is False
        assert _budget_api_allowed("PATCH", path) is False


def test_member_cannot_write_contacts():
    assert _budget_api_allowed("POST", "/api/persons") is False
    assert _budget_api_allowed("DELETE", f"/api/persons/{PID}") is False


def test_member_company_list_allowed_but_not_detail_or_subroutes():
    CID = "22222222-2222-2222-2222-222222222222"
    # the reduced/scoped list is allowed…
    assert _budget_api_allowed("GET", "/api/companies") is True
    # …but NOT the detail (carries opp/pipeline), link-suggestions, or writes
    assert _budget_api_allowed("GET", f"/api/companies/{CID}") is False
    assert _budget_api_allowed("GET", "/api/companies/link-suggestions") is False
    assert _budget_api_allowed("PATCH", f"/api/companies/{CID}/sharing") is False
    assert _budget_api_allowed("POST", "/api/companies") is False


def test_existing_allowances_unchanged():
    assert _budget_api_allowed("GET", "/api/projects") is True
    assert _budget_api_allowed("GET", "/api/finance/accounts") is True
    assert _budget_api_allowed("GET", "/api/opportunities") is False
