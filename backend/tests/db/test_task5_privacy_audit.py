"""Task 5 step 5: privacy audit of DOB, ECB and 311 storage with real logins and sentinels.

One SYNTHETIC snapshot (DOB complaints, ECB summonses, 311 requests for one resolved
building) whose source rows carry distinct sentinel values in every private field:
a DOB unit in the address and DOB's extract time; ECB respondent name and address,
inspector text and hearing time; 311 request key, address, streets, landmark,
coordinates, a phone number, an e-mail address and a caller name. Then, as each real
login:

- every column it can SELECT, in every schema, is read and walked recursively for every
  sentinel and every private source key (Task 4 keys plus the 0011 additions);
- the evidence identity view shows 311 tokens, never the request key;
- direct-SQL bypass attempts are refused (permission or lifecycle);
- write errors and every log record (SQLAlchemy engine logger at DEBUG) carry no
  sentinel; positive control: every sentinel IS in evidence.raw_row for evidence_admin.
"""

# ruff: noqa: S608  (relation and column names come from the catalog; no input)

import logging
import traceback

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.redaction import K311_PATTERN
from nyc_apartment_agent.storage import repository as repo
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import PostgresEvidenceReader
from nyc_apartment_agent.storage.repository import RecordWriteError, save_research_snapshot
from tests.db import full_snapshot as fs
from tests.db.test_privacy_audit import _VISIBLE_COLUMNS, walk
from tests.db.test_task5_repository import LOADERS, TYPES
from tests.unit import test_dob_complaints as dobt
from tests.unit import test_ecb_violations as ecbt
from tests.unit import test_sr311 as srt

pytestmark = pytest.mark.db

KEY_TEXT = "SENTINEL-T5-DIGEST-KEY"
SETTINGS = Settings(_env_file=None, evidence_digest_key=KEY_TEXT)
KEY = KEY_TEXT.encode()
UNIQUE_KEYS = ("987654321", "987654322")
SENTINELS = {
    "dob unit and extract time": ("7QZ", "SENTINEL-RUNDATE"),
    "ecb respondent": (
        "SENTINEL-RESP-NAME",
        "SENTINEL-RHN",
        "SENTINEL-RSTREET",
        "SENTINEL-RCITY",
        "SENTINEL-RZIP",
    ),
    "ecb inspector text": ("SENTINEL-INSPECTOR-TEXT",),
    "311 request key": UNIQUE_KEYS,
    "311 location": (
        "SENTINEL-311-ADDR",
        "SENTINEL-311-STREET",
        "SENTINEL-X1",
        "SENTINEL-X2",
        "SENTINEL-I1",
        "SENTINEL-LANDMARK",
        "40.8371234",
        "-73.8810987",
    ),
    "311 caller detail": ("SENTINEL-CALLER", "212-555-0199", "sentinel.person@example.org"),
    "credential": (KEY_TEXT,),
}
ALL = tuple(v for values in SENTINELS.values() for v in values)
STORED_PRIVATELY = tuple(v for k, vs in SENTINELS.items() if k != "credential" for v in vs)
T5_PRIVATE_KEYS = {
    "respondent_name",
    "respondent_house_number",
    "respondent_street",
    "respondent_city",
    "respondent_zip",
    "violation_description",
    "hearing_time",
    "incident_address",
    "street_name",
    "cross_street_1",
    "cross_street_2",
    "intersection_street_1",
    "intersection_street_2",
    "landmark",
    "latitude",
    "longitude",
    "location",
    "x_coordinate_state_plane",
    "y_coordinate_state_plane",
    "incident_zip",
    "city",
    "dobrundate",
    ":id",
}
PRIVATE_KEYS = fs.PRIVATE_KEYS | T5_PRIVATE_KEYS
ROLES_WITHOUT_CONTENT = ("app_reader", "ingest_writer", "evidence_retention", "evidence_restorer")


def build():
    buffer = BufferedEvidenceStore()
    kw = {"evidence": buffer, "digest_key": KEY}
    dob, _ = dobt.retrieve(
        [dobt.dob_row(1, house_street="TEST STREET APT 7QZ", dobrundate="SENTINEL-RUNDATE")],
        **kw,
    )
    ecb, _ = ecbt.retrieve(
        [
            ecbt.ecb_row(
                1,
                respondent_name="SENTINEL-RESP-NAME",
                respondent_house_number="SENTINEL-RHN",
                respondent_street="SENTINEL-RSTREET",
                respondent_city="SENTINEL-RCITY",
                respondent_zip="SENTINEL-RZIP",
                violation_description="SENTINEL-INSPECTOR-TEXT IN APT 9Z",
                hearing_time="0941",
            )
        ],
        **kw,
    )
    where = {"latitude": "40.8371234", "longitude": "-73.8810987"}
    sr, _ = srt.retrieve(
        [
            srt.sr_row(
                1,
                unique_key=UNIQUE_KEYS[0],
                incident_address="SENTINEL-311-ADDR",
                street_name="SENTINEL-311-STREET",
                cross_street_1="SENTINEL-X1",
                cross_street_2="SENTINEL-X2",
                intersection_street_1="SENTINEL-I1",
                landmark="SENTINEL-LANDMARK",
                location=where,
                resolution_description=srt.NYPD_TEMPLATE + " Call SENTINEL-CALLER 212-555-0199.",
                descriptor="sentinel.person@example.org",
                **where,
            ),
            srt.sr_row(2, unique_key=UNIQUE_KEYS[1]),
        ],
        **kw,
    )
    return (
        dobt.resolution(),
        {"dob_complaints": dob, "dob_ecb_violations": ecb, "service_requests_311": sr},
        buffer,
    )


@pytest.fixture
def saved(db):
    res, results, buffer = build()
    run_id, saved = save_research_snapshot(
        db["writer"], res, "1 SYNTHETIC ROAD", list(results.values()), buffer.items(), SETTINGS
    )
    return results, buffer, saved


def scan_as(engine):
    findings, seen, relations = [], 0, set()
    with engine.connect() as conn:
        for schema, relation, column in conn.execute(_VISIBLE_COLUMNS).all():
            relations.add(f"{schema}.{relation}")
            q = f'SELECT to_jsonb("{column}") FROM "{schema}"."{relation}"'
            for (doc,) in conn.execute(text(q)):
                for path, s in walk(doc):
                    seen += 1
                    where = f"{schema}.{relation}.{column}:{'.'.join(map(str, path))}"
                    findings += [
                        (kind, v, where) for kind, vs in SENTINELS.items() for v in vs if v in s
                    ]
                    is_key = bool(path) and s == path[-1]
                    parent = path[-2] if len(path) > 1 else None
                    if is_key and s in PRIVATE_KEYS and parent not in fs.FIELD_NAME_LISTS:
                        findings.append(("private key", s, where))
    return relations, seen, findings


def test_no_login_without_content_rights_can_read_a_task5_sentinel(db, saved, caplog):
    results, buffer, stored = saved
    caplog.set_level(logging.DEBUG)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.DEBUG)
    try:
        with db["app_reader"].connect() as conn:
            loaded = {t: LOADERS[t](conn, stored[t].fetch_id) for t in TYPES}
        reader = PostgresEvidenceReader(db["evidence_admin"], KEY)
        assert all(reader.get(i.original_digest) is not None for i in buffer.items())
        surfaces = {role: scan_as(db[role]) for role in ROLES_WITHOUT_CONTENT}
    finally:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.NOTSET)
    assert {t: s.result for t, s in loaded.items()} == results
    for role, (_, _, findings) in surfaces.items():
        assert findings == [], role
    rel, seen, _ = surfaces["app_reader"]
    assert "records.normalized_record" in rel and not any(r.startswith("evidence.") for r in rel)
    assert seen > 500
    assert surfaces["ingest_writer"][0] == rel | {"records.evidence_identity"}
    assert surfaces["evidence_retention"][0] == {"evidence.raw_row"}
    assert surfaces["evidence_restorer"][:2] == (set(), 0)
    assert caplog.records
    for s in ALL:
        assert s not in caplog.text, s


def test_task5_sentinels_are_held_only_in_raw_evidence(db, saved):
    with db["evidence_admin"].connect() as conn:
        raw = " ".join(
            conn.execute(text("select original_row::text from evidence.raw_row")).scalars()
        )
    for s in STORED_PRIVATELY:
        assert s in raw, s
    with db["owner"].connect() as conn:
        everything = conn.execute(
            text("select string_agg(to_jsonb(r)::text, ' ') from evidence.raw_row r")
        ).scalar()
    assert KEY_TEXT not in everything  # the digest key is stored nowhere


def test_evidence_identity_shows_311_tokens_only(db, saved):
    results, _, _ = saved
    with db["ingest_writer"].connect() as conn:
        ids = set(
            conn.execute(
                text(
                    "select source_record_id from records.evidence_identity "
                    "where dataset_id = 'erm2-nwe9'"
                )
            ).scalars()
        )
    assert ids == {r.request_ref for r in results["service_requests_311"].records}
    assert all(K311_PATTERN.match(i) for i in ids) and not ids & set(UNIQUE_KEYS)


def test_direct_sql_bypass_attempts_are_refused(db, saved):
    results, _, _ = saved
    token = results["service_requests_311"].records[0].request_ref
    digest = results["service_requests_311"].records[0].original_digest
    attempts = [
        (
            "app_reader",
            "insert into records.normalized_record (id) values (gen_random_uuid())",
            "permission denied",
        ),
        ("app_reader", "select original_row from evidence.raw_row", "permission denied"),
        ("app_reader", "select * from records.evidence_identity", "permission denied"),
        ("ingest_writer", "select original_row from evidence.raw_row", "permission denied"),
        (
            "ingest_writer",
            "update records.normalized_record set payload = '{}'",
            "permission denied",
        ),
        ("ingest_writer", "delete from records.normalized_record", "permission denied"),
        (
            "ingest_writer",
            f"update evidence.raw_row set source_record_id = '987654321' "
            f"where original_digest = '{digest}'",
            "permission denied",
        ),
        ("evidence_retention", "select original_row from evidence.raw_row", "permission denied"),
        (
            "evidence_retention",
            f"update evidence.raw_row set source_record_id = '987654321' "
            f"where original_digest = '{digest}'",
            "permission denied",
        ),
        (
            "evidence_retention",
            'update evidence.raw_row set original_row = \'{"unique_key": "987654321"}\'',
            "held raw evidence cannot be changed",
        ),
        ("evidence_restorer", "select source_record_id from evidence.raw_row", "permission denied"),
        (
            "evidence_restorer",
            f"select evidence.restore_expired_raw_row('{digest}', "
            f"'erm2-nwe9', '{token}', 'original_row', 'x', 'y', '{{}}'::jsonb, now(), 'u', "
            "null, now() + interval '1 day')",
            "no expired evidence matches",
        ),
        (
            "evidence_admin",
            "insert into records.retrieval_run (id) values (gen_random_uuid())",
            "permission denied",
        ),
        ("evidence_admin", "delete from evidence.raw_row", "permission denied"),
    ]
    outcomes = {}
    for role, sql, expected in attempts:
        try:
            with db[role].begin() as conn:
                conn.execute(text(sql))
            outcomes[(role, sql)] = "ALLOWED"
        except DBAPIError as exc:
            outcomes[(role, sql)] = "ok" if expected in str(exc.orig) else str(exc.orig)[:120]
    assert {k: v for k, v in outcomes.items() if v != "ok"} == {}


def _all_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc)) + repr(exc.__context__) + repr(exc.__cause__)


def test_write_errors_and_logs_carry_no_task5_sentinel(db, monkeypatch, caplog):
    res, results, buffer = build()
    real = repo.record_payload

    def leaky(record):  # a writer bug: the 311 address reaches the public payload
        payload = real(record)
        if record.dataset_id == "erm2-nwe9":
            payload["incident_address"] = "SENTINEL-311-ADDR"
            payload["descriptor"] = UNIQUE_KEYS[0]
        return payload

    monkeypatch.setattr(repo, "record_payload", leaky)
    caplog.set_level(logging.DEBUG)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.DEBUG)
    try:
        with pytest.raises(RecordWriteError) as info:
            save_research_snapshot(
                db["writer"],
                res,
                "1 SYNTHETIC ROAD",
                list(results.values()),
                buffer.items(),
                SETTINGS,
            )
    finally:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.NOTSET)
    blob = _all_text(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None
    for s in ALL:
        assert s not in blob, s
        assert s not in caplog.text, s
    with db["owner"].connect() as conn:  # and nothing was stored
        assert conn.execute(text("select count(*) from evidence.raw_row")).scalar() == 0
