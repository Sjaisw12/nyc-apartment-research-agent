"""HPD registration contacts (Task 3, step 3) on SYNTHETIC rows. All names are invented."""

import datetime as dt
import logging

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.admin_models import ContactRole, ContactSourceResult
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verifies_original_row
from nyc_apartment_agent.records.hpd_contacts import retrieve_hpd_contacts
from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations
from nyc_apartment_agent.records.models import (
    Attribution,
    DigestBasis,
    EvidenceKind,
    RetrievalStatus,
)
from nyc_apartment_agent.records.redaction import sanitize_contact
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import (
    NEWER_TRUTH,
    FakeSocrata,
    contact_row,
    person_contact,
    registration_row,
    resolution,
)

TODAY = dt.date(2026, 10, 22)
KEY = b"synthetic-test-key"
SECRETS = ("ZEBULON", "SYNTHETICSON", "PRESIDENT", "SUITE 9", "SYNTHETIC AVENUE", "BROOKLYN")


def registrations(rows, res=None, fake=None):
    fake = fake or FakeSocrata(rows)
    with fake.client() as c:
        return retrieve_hpd_registrations(
            res or resolution(), c, today=TODAY, digest_key=KEY, page_size=10
        )


def contacts(rows, regs=None, res=None, fake=None, **kw):
    regs = regs if regs is not None else registrations([registration_row("r-1")], res=res)
    fake = fake or FakeSocrata(rows)
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 10)
    with fake.client() as c:
        result = retrieve_hpd_contacts(
            res or resolution(), c, registrations=regs, today=TODAY, **kw
        )
    return result, fake


# ---- statuses, batches, independence -------------------------------------------------


def test_records_found_with_evidence_kind_and_role_note():
    result, _ = contacts([contact_row("c-01"), person_contact("c-02")])
    assert result.status is RetrievalStatus.RECORDS_FOUND
    r = result.records[0]
    assert r.evidence_kind is EvidenceKind.ADMINISTRATIVE_RECORD
    assert "not independent proof of legal title" in r.role_note


def _many_regs(n):
    return registrations(
        [
            registration_row(f"r-{i:02d}", reg=i, bid=i, bin=str(2006185 + i))
            for i in range(1, n + 1)
        ]
    )


def test_ids_are_batched_and_each_batch_is_a_verified_fetch():
    regs = _many_regs(7)
    rows = [contact_row(f"c-{i:02d}", reg=i) for i in range(1, 8)]
    result, fake = contacts(rows, regs=regs, batch_size=3)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert len(result.batch_provenance) == 3
    assert result.provenance.source_count == 7 == len(result.records)
    wheres = {r.url.params["$where"] for r in fake.calls if "$where" in r.url.params}
    assert wheres == {
        "registrationid in (1,2,3)",
        "registrationid in (4,5,6)",
        "registrationid in (7)",
    }
    pages = [r for r in fake.calls if "$offset" in r.url.params]
    assert all(r.url.params["$order"] == ":id" for r in pages)


def test_a_failed_batch_marks_contacts_incomplete_and_keeps_verified_rows():
    regs = _many_regs(4)
    rows = [contact_row(f"c-{i:02d}", reg=i) for i in range(1, 5)]
    fake = FakeSocrata(rows)
    fake.fail_at = {2: 503, 3: 503}  # batch 0 ok (calls 0-1); batch 1's count fails twice
    reg_status_before = regs.status
    result, _ = contacts([], regs=regs, fake=fake, batch_size=2)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and result.partial
    assert {r.registration_id for r in result.records} == {"1", "2"}
    assert any(r.startswith("batch_1_unavailable") for r in result.provenance.incomplete_reasons)
    assert regs.status is reg_status_before  # the registration result is untouched


def test_all_batches_failing_is_source_unavailable():
    fake = FakeSocrata([contact_row("c-01")])
    fake.fail_at = {0: 503, 1: 503}
    result, _ = contacts([], fake=fake)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE and result.records == []


def test_dataset_change_between_batches_is_incomplete():
    regs = _many_regs(2)
    fake = FakeSocrata([contact_row("c-01", reg=1), contact_row("c-02", reg=2)])
    fake.truth_at = {2: NEWER_TRUTH}  # second batch sees a newer version throughout
    result, _ = contacts([], regs=regs, fake=fake, batch_size=1)
    assert "dataset_changed_between_batches" in result.provenance.incomplete_reasons


def test_registrations_unavailable_means_no_lookup():
    fake = FakeSocrata([registration_row("r-1")])
    fake.fail_at = {0: 503, 1: 503}
    regs = registrations([], fake=fake)
    result, cfake = contacts([contact_row("c-01")], regs=regs)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE and cfake.calls == []


def test_no_registration_on_file_means_no_contacts_and_no_calls():
    result, fake = contacts([contact_row("c-01")], regs=registrations([]))
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND and fake.calls == []
    assert "no registration on file" in result.status_detail


def test_incomplete_registrations_make_contacts_incomplete():
    fake = FakeSocrata([registration_row(f"r-{i:02d}", reg=i, bid=i) for i in range(1, 16)])
    fake.fail_at = {2: 500, 3: 500}
    regs = registrations([], fake=fake)
    assert regs.status is RetrievalStatus.INCOMPLETE_PAGINATION
    rows = [contact_row(f"c-{i:02d}", reg=i) for i in range(1, 11)]
    result, _ = contacts(rows, regs=regs)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.provenance.incomplete_reasons[0].startswith("registration_list_incomplete")


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unsupported_join_makes_zero_requests(outcome):
    res = resolution(outcome=outcome)
    regs = registrations([], res=res)
    result, fake = contacts([contact_row("c-01")], regs=regs, res=res)
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN and fake.calls == []


def test_invalid_registration_ids_are_never_put_into_a_query():
    regs = registrations(
        [registration_row("r-1"), registration_row("r-2", reg="12) OR (1=1", bid=2)]
    )
    result, fake = contacts([contact_row("c-01")], regs=regs)
    assert all("OR" not in r.url.params.get("$where", "") for r in fake.calls)
    assert "invalid_registration_ids_skipped: 1" in result.provenance.incomplete_reasons


# ---- privacy (D11) -------------------------------------------------------------------


def test_person_names_titles_and_unit_level_addresses_never_reach_public_output():
    store = InMemoryEvidenceStore()
    result, _ = contacts(
        [
            contact_row("c-01"),
            person_contact("c-02"),
            person_contact("c-03", type_="Agent", corporationname="AGENT CO"),
        ],
        evidence=store,
    )
    dumped = result.model_dump_json()
    for secret in SECRETS:
        assert secret not in dumped
    corp, person, agent = result.records
    assert corp.corporation_name == "SYNTHETIC OWNER LLC" and not corp.names_a_person
    assert (corp.public_city, corp.public_state, corp.public_zip) == ("NEW YORK", "NY", "10001")
    assert person.names_a_person and person.public_city is None and person.public_zip is None
    assert agent.corporation_name == "AGENT CO" and agent.names_a_person
    assert "firstname" in person.withheld_fields and "businessapartment" in corp.withheld_fields
    # The controlled evidence store keeps the original.
    item = store.get(person.original_digest)
    assert item.original_row["lastname"] == "SYNTHETICSON"
    assert verifies_original_row(item, KEY)


def test_individual_owner_is_shown_as_role_with_name_withheld():
    result, _ = contacts([person_contact("c-01", type_="IndividualOwner")])
    r = result.records[0]
    assert r.role is ContactRole.INDIVIDUAL_OWNER and r.corporation_name is None
    assert r.names_a_person
    summary = result.summary.by_registration[0]
    assert summary.persons_named_withheld == 1 and summary.corporate_names_by_role == {}


def test_logs_never_contain_contact_details(caplog):
    caplog.set_level(logging.DEBUG)
    contacts([person_contact("c-01")])
    fake = FakeSocrata([person_contact("c-01")])
    fake.fail_at = {0: 503, 1: 503}
    contacts([], fake=fake)
    for secret in SECRETS:
        assert secret not in caplog.text


def test_sanitized_contact_replays_with_sanitized_digest_basis_and_same_public_view():
    raw = person_contact("c-01")
    sanitized, withheld = sanitize_contact(raw)
    assert "lastname" in withheld and sanitize_contact(sanitized)[1] == []  # idempotent
    live, _ = contacts([raw])
    replay, _ = contacts([sanitized])
    a, b = live.records[0], replay.records[0]
    assert (a.digest_basis, b.digest_basis) == (DigestBasis.ORIGINAL_ROW, DigestBasis.SANITIZED_ROW)
    assert b.names_a_person and b.public_city is None
    assert a.withheld_fields == b.withheld_fields
    assert a.redacted_sha256 == b.redacted_sha256 and a.original_digest != b.original_digest


# ---- roles, attribution, duplicates --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "role"),
    [
        ("CorporateOwner", ContactRole.CORPORATE_OWNER),
        ("IndividualOwner", ContactRole.INDIVIDUAL_OWNER),
        ("JointOwner", ContactRole.JOINT_OWNER),
        ("HeadOfficer", ContactRole.HEAD_OFFICER),
        ("Officer", ContactRole.OFFICER),
        ("Shareholder", ContactRole.SHAREHOLDER),
        ("Agent", ContactRole.AGENT),
        ("SiteManager", ContactRole.SITE_MANAGER),
        ("Lessee", ContactRole.LESSEE),
        ("Trustee", ContactRole.OTHER),
    ],
)
def test_roles_map_one_to_one_and_unknown_stays_other_with_raw(raw, role):
    result, _ = contacts([contact_row("c-01", type_=raw)])
    assert result.records[0].role is role and result.records[0].role_raw == raw


def test_shared_registration_contacts_apply_to_every_covered_building():
    regs = registrations(
        [registration_row("r-1", bid=1), registration_row("r-2", bid=2, bin="2006186")]
    )
    result, _ = contacts([contact_row("c-01")], regs=regs)
    (summary,) = result.summary.by_registration
    assert summary.building_ids == ["1", "2"]
    assert summary.attribution is Attribution.THIS_BUILDING
    assert result.summary.requested_building == [summary]


def test_tax_lot_contacts_are_lot_level_and_name_no_requested_building():
    res = resolution(level=ResolutionLevel.TAX_LOT)
    result, _ = contacts([contact_row("c-01")], res=res)
    assert result.records[0].attribution is Attribution.LOT_LEVEL
    assert result.summary.requested_building is None
    data = result.model_dump()
    data["records"][0]["attribution"] = "this_building"
    with pytest.raises(ValidationError, match="building-level attribution"):
        ContactSourceResult.model_validate(data)


def test_model_rejects_address_detail_for_a_contact_naming_a_person():
    result, _ = contacts([person_contact("c-01")])
    data = result.model_dump()
    data["records"][0]["public_city"] = "BROOKLYN"
    with pytest.raises(ValidationError, match="naming a person"):
        ContactSourceResult.model_validate(data)


def test_identical_copies_collapse_and_reused_contact_ids_are_counted():
    rows = [
        contact_row("c-01"),
        contact_row("c-02", registrationcontactid="20362501"),  # same content as c-01
        contact_row("c-03", registrationcontactid="20362501", type_="Agent"),
    ]
    result, _ = contacts(rows)
    assert len(result.records) == 2
    assert result.summary.duplicate_contact_ids == 1
