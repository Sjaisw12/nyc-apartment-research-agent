"""Task 5 records: DOB complaints, DOB ECB violations, 311 service requests (Task 5, step 5).

Additive on top of 0010. The three Task 5 results are stored in the Task 4 tables
(source_fetch, fetch_page, normalized_record, record_source_row, evidence.raw_row); no
new table, role or grant. What changes:

Vocabularies (CHECKs replaced with supersets; every existing value stays valid)
  - result_type   + dob_complaints, dob_ecb_violations, service_requests_311
  - record_kind   + dob_complaint, ecb_violation, service_request_311
  - evidence_kind + enforcement_action (ECB summonses, and nothing else)
  - lot_membership + via_bin_mapping (a row with no lot, placed on the lot by its BIN)
  - attribution_basis + bin_lot_mapping (DOB complaints on another building of the lot)
  Each Task 5 result type holds only its own dataset and record kind, and vice versa.

Private keys (records.forbidden_keys_absent, used by every public JSONB CHECK)
  Adds, at any depth: ECB respondent name and address fields, violation_description and
  hearing_time; 311 location fields (address, streets, landmark, coordinates, location,
  zip, city, channel...); DOB's dobrundate; and the Socrata row-id key ":id". The
  upgrade refuses to finish if any stored payload already holds one of these keys.

Shape (top-level and nested key whitelists, by jsonpath)
  Each Task 5 record payload may hold only its model's fields; source_rows entries,
  order events, infractions, redaction info and date values likewise. Each Task 5
  result payload, its provenance and its summary may hold only their model's fields.
  So a writer that can INSERT cannot add a new private field, at any level we name.
  Strings in Task 5 payloads may not look like an e-mail address or a phone number.

311 identifier contract (design 18.1)
  normalized_record: source_record_id ~ '^k311_[0-9a-f]{32}$'; payload request_ref and
  source_record_id equal it; payload source_row_id is null; no source_rows; dates are
  date-only; no string anywhere in the payload names a Socrata ":id", carries a
  unique_key value in a filter, or is a bare 7 to 9 digit number (the shape of a
  unique_key). evidence.raw_row: a 311 raw row's source_record_id is the token. The
  existing 0006 rules keep 311 records single-row (one link, source_row_id NULL,
  link source id = the token) and 0007's link-identity check ties that link to the raw
  row stored under the same token. fetch_page URLs of a 311 fetch never carry ":id" or a
  unique_key filter, and the stored where clause names neither.

DOB and ECB rules
  - DOB: building attribution only via_bin_mapping and never with a BIN-lot conflict or
    conflicting duplicates; other_building_on_lot exactly when the basis is
    bin_lot_mapping; unknown_code goes with disposition_code_unknown and no description;
    an order_event outcome has an order event; no order event anywhere says it is in
    effect (still_in_effect is only "not_shown_by_source").
  - ECB: enforcement_action; attribution by its own row identifiers; via_bin_mapping
    only for a resolved-BIN row with no lot parts; building attribution never with a
    blocking flag; unknown adjudication flagged status_unknown; CLASS-n severities go
    with severity_undocumented_value; a law text only with its allowlisted code.
  - Requested-building claims (DOB, ECB summaries) need building scope and a resolved
    BIN with no lot conflict; ECB requested-building financials need those claims and,
    at commit, count exactly the this_building records.

Lineage (records.check_fetch_complete, replaced)
  DOB complaints and ECB summonses carry row-level lineage like registrations: one link
  per source row in source_rows, source_record_id = the first row's id (DOB: its Socrata
  :id; ECB: its ISN). Their source_rows entries carry extra fields (query_url,
  fetched_at, and for ECB row_index), so the payload is compared with the links by
  projection on (source_row_id, original_digest); registrations and contacts keep the
  exact comparison. The 0006 records_found rules apply unchanged: every link a distinct
  raw row and a distinct source_row_id, links = source_count. (Identical ECB copies share
  one ISN and one keyed digest; the adapter marks such a fetch incomplete because its
  paging key repeated, and 0006 allows a raw row to be linked twice only then.)

Commit-time (records.check_task5_fetch, new)
  311 records_found: the collapsed copies add up to the source count, and a record has
  more than one copy exactly when it is flagged duplicate_id_same_content. 311 record
  scope equals the fetch's scope. ECB financials count the this_building records.

311 result rules (source_fetch CHECK)
  No BIN; never building claims; summary.complete exactly when there are no incomplete
  reasons; an excluded count is established exactly when it gives counts, and the
  HPD/DOB count only for a complete result on the accepted data version; NULL-agency
  rows found -> HPD/DOB count not established and a null_agency_rows_not_retrieved
  reason; NULL-agency count not established -> a null_agency_coverage_unknown warning;
  an accepted data version only when every reason is a NULL-agency reason; records_found
  only with an accepted version equal to data_as_of and an accepted last attempt.

Downgrade refuses while any Task 5 fetch, record or raw row exists, then removes only
what this migration adds and restores the 0010 definitions exactly.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-23
"""

# ruff: noqa: S608  (SQL assembled from constant fragments only; no input)

import importlib.util
from pathlib import Path

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _sibling(name: str):
    """Load an earlier migration module (to restore its definitions on downgrade)."""
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


K311_PATTERN = "'^k311_[0-9a-f]{32}$'"
DOB, ECB, SR311 = "dob_complaint", "ecb_violation", "service_request_311"
T5_TYPES = {  # result_type -> (dataset, record_kind)
    "dob_complaints": ("eabe-havv", DOB),
    "dob_ecb_violations": ("6bgk-3dad", ECB),
    "service_requests_311": ("erm2-nwe9", SR311),
}
T5_DATASETS = "'eabe-havv', '6bgk-3dad', 'erm2-nwe9'"
T5_KINDS = f"'{DOB}', '{ECB}', '{SR311}'"
T5_RESULT_TYPES = ", ".join(f"'{t}'" for t in T5_TYPES)

OLD_RESULT_TYPES = (
    "'hpd_violations', 'hpd_complaints', 'hpd_buildings', 'hpd_registrations', 'hpd_contacts'"
)
OLD_RECORD_KINDS = "'violation', 'complaint_problem', 'hpd_building', 'registration', 'contact'"
OLD_EVIDENCE_KINDS = "'official_finding', 'reported_allegation', 'administrative_record'"
OLD_LOT_MEMBERSHIP = "'confirmed', 'conflicting'"
OLD_BASES = "'row_identifiers', 'hpd_building_id', 'registration_coverage'"

# Keys refused at any depth of any public JSONB value, in addition to 0007's.
T5_PRIVATE = (
    # ECB (D22, D31): respondent fields, inspector free text, hearing time
    "respondent_name",
    "respondent_house_number",
    "respondent_street",
    "respondent_city",
    "respondent_zip",
    "violation_description",
    "hearing_time",
    # 311: location and channel fields of the source row (D20, 18.1)
    "incident_address",
    "street_name",
    "cross_street_1",
    "cross_street_2",
    "intersection_street_1",
    "intersection_street_2",
    "address_type",
    "landmark",
    "city",
    "incident_zip",
    "latitude",
    "longitude",
    "location",
    "x_coordinate_state_plane",
    "y_coordinate_state_plane",
    "open_data_channel_type",
    "taxi_pick_up_location",
    # DOB complaints: DOB's extract time (private evidence only)
    "dobrundate",
    # Socrata row id as a key (a row locator); never in a public value
    ":id",
)

# Field whitelists, frozen from the step 2-4 models (schema_version 1).
DATE = ("raw", "value")
REDACTION = ("applied", "fields_masked", "text_fields_redacted", "text_replacements")
PROVENANCE = (
    "data_as_of",
    "dataset_id",
    "error",
    "incomplete_reasons",
    "order_by",
    "page_size",
    "restarts",
    "row_cap",
    "rows_fetched",
    "source_count",
    "warnings",
    "where",
)
DOB_RECORD = (
    "attribution",
    "attribution_basis",
    "bin_sources",
    "community_board",
    "complaint_category",
    "complaint_category_description",
    "complaint_category_priority",
    "complaint_number",
    "complaint_status",
    "data_as_of",
    "dataset_id",
    "digest_basis",
    "disposition_code",
    "disposition_date",
    "disposition_definition_source",
    "disposition_description",
    "dob_unit",
    "evidence_kind",
    "fetched_at",
    "inspection_date",
    "lot_membership",
    "order_event",
    "original_digest",
    "outcome",
    "quality_flags",
    "query_url",
    "received_date",
    "recorded_address",
    "recorded_zip_code",
    "redacted_sha256",
    "redaction",
    "resolved_bbl",
    "source_bin",
    "source_rows",
    "status_raw",
)
DOB_SOURCE_ROW = ("fetched_at", "original_digest", "query_url", "source_row_id")
ORDER_EVENT = (
    "action",
    "complaint_number",
    "disposition_code",
    "disposition_date",
    "dob_text",
    "kind",
    "statement",
    "still_in_effect",
)
ECB_RECORD = (
    "adjudication",
    "adjudication_note",
    "aggravated_level",
    "amount_paid",
    "attribution",
    "attribution_basis",
    "balance_due",
    "certification_status",
    "correction_status",
    "correction_status_raw",
    "data_as_of",
    "dataset_id",
    "digest_basis",
    "dob_violation_number",
    "ecb_violation_number",
    "evidence_kind",
    "fetched_at",
    "hearing_date",
    "hearing_status_raw",
    "infractions",
    "isn_dob_bis_extract",
    "issue_date",
    "lot_format",
    "lot_membership",
    "matched_by",
    "original_digest",
    "penalty_imposed",
    "quality_flags",
    "query_url",
    "redacted_sha256",
    "resolved_bbl",
    "row_bbl",
    "served_date",
    "severity",
    "severity_raw",
    "source_bin",
    "source_block",
    "source_boro",
    "source_lot",
    "source_rows",
    "violation_type",
    "withheld_fields",
)
ECB_SOURCE_ROW = ("fetched_at", "original_digest", "query_url", "row_index", "source_row_id")
INFRACTION = ("code", "law_description", "position", "text_withheld")
SR311_RECORD = (
    "agency",
    "agency_updated_date",
    "attribution",
    "attribution_basis",
    "closed_date",
    "collapsed_copies",
    "complaint_type",
    "data_as_of",
    "dataset_id",
    "descriptor",
    "descriptor_2",
    "digest_basis",
    "evidence_kind",
    "fetched_at",
    "join_scope",
    "location_type",
    "lot_membership",
    "original_digest",
    "quality_flags",
    "query_url",
    "received_date",
    "redacted_sha256",
    "redaction",
    "request_ref",
    "request_status",
    "resolution_description",
    "resolved_bbl",
    "source_bbl",
    "source_record_id",
    "source_row_id",
    "status_raw",
    "withheld_fields",
)
DOB_RESULT = (
    "bin_evidence",
    "bin_evidence_complete",
    "bin_evidence_provenance",
    "bin_lot_conflicts",
    "coverage_notes",
    "dataset_id",
    "join_scope",
    "lot_family",
    "partial",
    "provenance",
    "resolved_bbl",
    "resolved_bin",
    "search_bins",
    "source",
    "status",
    "status_detail",
    "summary",
)
ECB_RESULT = (
    "bin_clause",
    "bin_evidence",
    "bin_evidence_complete",
    "bin_evidence_provenance",
    "bin_lot_conflicts",
    "coverage_notes",
    "dataset_id",
    "join_scope",
    "lot_family",
    "lot_formats_queried",
    "partial",
    "provenance",
    "resolved_bbl",
    "resolved_bin",
    "row_bins_not_on_lot",
    "source",
    "status",
    "status_detail",
    "summary",
)
SR311_RESULT = (
    "accepted_data_version",
    "attempts",
    "coverage_notes",
    "data_versions_seen",
    "dataset_id",
    "excluded_counts",
    "join_scope",
    "lot_family",
    "lot_family_provenance",
    "partial",
    "provenance",
    "resolved_bbl",
    "resolved_bin",
    "rows_rejected",
    "source",
    "status",
    "status_detail",
    "summary",
)
DOB_SUMMARY = (
    "active_with_final_disposition",
    "building_claims_denied_reason",
    "building_specific_claims_allowed",
    "by_attribution",
    "closed_with_no_disposition",
    "conflict_counts",
    "data_as_of",
    "join_scope",
    "order_events",
    "quality_flag_counts",
    "source_rows",
    "status_fields_disagree",
    "total_complaints",
    "unknown_category_codes",
    "unknown_disposition_codes",
)
ECB_SUMMARY = (
    "adjudication_notes",
    "building_claims_denied_reason",
    "building_specific_claims_allowed",
    "by_attribution",
    "conflict_counts",
    "data_as_of",
    "join_scope",
    "lot_format_counts",
    "matched_by_counts",
    "quality_flag_counts",
    "requested_building_financials",
    "source_rows",
    "statement",
    "status_fields_disagree",
    "total_summonses",
)
SR311_SUMMARY = (
    "building_specific_claims_allowed",
    "by_attribution",
    "closed_without_closed_date",
    "complete",
    "data_as_of",
    "join_scope",
    "open_with_closed_date",
    "quality_flag_counts",
    "source_rows",
    "statement",
    "status_fields_disagree",
    "total_requests",
    "withheld_value_counts",
)
EXCLUDED = ("agencies", "by_agency", "note", "null_agency")
COUNT_CHECK = (
    "counts",
    "counts_observed",
    "error",
    "established",
    "fetched_at",
    "not_established_reason",
    "ok",
    "query_where",
    "truth_header",
)
ATTEMPT = ("accepted", "attempt", "count_header", "page_headers", "reasons", "rows", "source_count")

# Strings that must not appear in a Task 5 payload: an e-mail address, a phone number.
CONTACT_LIKE = (
    '@ like_regex "[a-z0-9._%+-]+@[a-z0-9.-]+[.][a-z][a-z]+" flag "i" '
    '|| @ like_regex "[0-9][0-9][0-9][-. )]+[0-9][0-9][0-9][-. ][0-9][0-9][0-9][0-9]"'
)
# Strings that must not appear in a 311 payload: a Socrata ":id", a unique_key value in a
# filter, or a bare number shaped like a unique_key (7 to 9 digits; BBLs have 10).
SR311_ID_LIKE = (
    '@ like_regex "(:|%3a)id" flag "i" '
    '|| @ like_regex "unique_key( |[+]|%20)*(=|<|>|%3d|%3c|%3e|in[^a-z_])" flag "i" '
    '|| @ like_regex "^[0-9]{7,9}$"'
)


def _keys_only(path: str, allowed: tuple[str, ...]) -> str:
    """jsonpath test: every key of the object(s) at `path` is in `allowed`."""
    alts = " || ".join(f'@.key == "{k}"' for k in allowed)
    return (
        f"NOT jsonb_path_exists({{col}}, "
        f"'lax {path} ? (@.type() == \"object\").keyvalue() ? (!({alts}))')"
    )


def _shape(col: str, rules: list[tuple[str, tuple[str, ...]]]) -> str:
    return "\n                AND ".join(_keys_only(p, a).format(col=col) for p, a in rules)


def _no_string(col: str, test: str) -> str:
    return f"NOT jsonb_path_exists({col}, 'lax $.** ? (@.type() == \"string\" && ({test}))')"


def _date_only(field: str) -> str:
    return (
        f"NOT jsonb_path_exists(payload, 'lax $.{field}.raw ? "
        f'(@.type() == "string" && !(@ like_regex "^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$"))\')'
    )


def _bind_safe(key: str) -> str:
    """op.execute() reads ':name' as a bind parameter; '\\:' is a literal colon."""
    return key.replace(":", "\\:")


def _forbidden_keys_fn(keys) -> str:
    tests = "\n                OR ".join(
        f"jsonb_path_exists(j, 'lax $.**.\"{_bind_safe(k)}\"')" for k in keys
    )
    return f"""
        CREATE OR REPLACE FUNCTION records.forbidden_keys_absent(j jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT NOT (
                {tests}
            )
        $fn$
    """


def upgrade() -> None:
    m7 = _sibling("0007_admin_records.py")
    with as_owner():
        op.execute(_forbidden_keys_fn(m7.OLD_FORBIDDEN + m7.CONTACT_PRIVATE + T5_PRIVATE))
        _refuse_existing_private_keys()
        _vocabularies()
        _record_checks()
        _fetch_checks()
        op.execute(
            "ALTER TABLE evidence.raw_row ADD CONSTRAINT sr311_raw_row_token CHECK ("
            f"dataset_id <> 'erm2-nwe9' OR source_record_id ~ {K311_PATTERN})"
        )
        _triggers()
        op.execute(_check_fetch_complete())
        _commit_checks()


def _refuse_existing_private_keys() -> None:
    """A stored payload with a newly forbidden key would make the new rule untrue."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM records.normalized_record
                       WHERE NOT records.forbidden_keys_absent(payload))
               OR EXISTS (SELECT 1 FROM records.source_fetch
                          WHERE NOT records.forbidden_keys_absent(
                              result_payload #- '{summary,withheld_field_counts}'))
               OR EXISTS (SELECT 1 FROM records.complaint_group
                          WHERE NOT records.forbidden_keys_absent(payload))
               OR EXISTS (SELECT 1 FROM records.registration_coverage
                          WHERE NOT records.forbidden_keys_absent(payload)) THEN
                RAISE EXCEPTION 'a stored payload holds a key 0011 forbids; not upgrading';
            END IF;
        END $$
        """
    )


def _replace_check(table: str, name: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr})")


def _vocabularies() -> None:
    _replace_check(
        "records.source_fetch",
        "source_fetch_result_type_check",
        f"result_type IN ({OLD_RESULT_TYPES}, {T5_RESULT_TYPES})",
    )
    _replace_check("records.source_fetch", "unsupported_join_made_no_requests", UNSUPPORTED_0011)
    nr = "records.normalized_record"
    _replace_check(
        nr,
        "normalized_record_record_kind_check",
        f"record_kind IN ({OLD_RECORD_KINDS}, {T5_KINDS})",
    )
    _replace_check(
        nr,
        "normalized_record_evidence_kind_check",
        f"evidence_kind IN ({OLD_EVIDENCE_KINDS}, 'enforcement_action')",
    )
    _replace_check(
        nr,
        "normalized_record_lot_membership_check",
        f"lot_membership IN ({OLD_LOT_MEMBERSHIP}, 'via_bin_mapping')",
    )
    _replace_check(
        nr,
        "normalized_record_attribution_basis_check",
        f"attribution_basis IN ({OLD_BASES}, 'bin_lot_mapping')",
    )
    _replace_check(
        nr, "row_lineage_shape", _row_lineage_shape(f"'registration', 'contact', '{DOB}', '{ECB}'")
    )


# 0004 requires an unsupported join to have no scope. DOB complaints are keyed by BIN only:
# at a verified tax lot the adapter makes no request and returns unsupported_join WITH the
# tax-lot scope and lot, to say why (step 2, accepted). Allowed for that case only.
UNSUPPORTED_0004 = "status <> 'unsupported_join' OR (page_count = 0 AND join_scope IS NULL)"
UNSUPPORTED_0011 = (
    "status <> 'unsupported_join' OR (page_count = 0 AND (join_scope IS NULL OR ("
    "result_type = 'dob_complaints' AND join_scope = 'tax_lot' AND resolved_bin IS NULL "
    "AND resolved_bbl IS NOT NULL AND record_count = 0)))"
)


def _row_lineage_shape(kinds: str) -> str:
    return f"""
        (record_kind IN ({kinds})) = (payload ? 'source_rows')
        AND CASE WHEN payload ? 'source_rows' THEN
                jsonb_typeof(payload -> 'source_rows') = 'array'
                AND payload #>> '{{source_rows,0,source_row_id}}'
                    IS NOT DISTINCT FROM source_record_id
                AND payload #>> '{{source_rows,0,original_digest}}'
                    IS NOT DISTINCT FROM original_digest
            ELSE true END
    """


def _record_checks() -> None:
    kind_dataset = " AND ".join(
        f"((record_kind = '{kind}') = (dataset_id = '{ds}'))" for ds, kind in T5_TYPES.values()
    )
    building = "attribution IN ('this_building', 'other_building_on_lot')"
    ecb_blocking = (
        "ARRAY['bin_lot_conflict', 'lot_identifier_conflict', 'duplicate_id_different_content', "
        "'bin_retired_or_unknown', 'bin_on_other_lot', 'bin_not_on_current_lot']"
    )
    dob_shape = _shape(
        "payload",
        [
            ("$", DOB_RECORD),
            ("$.source_rows[*]", DOB_SOURCE_ROW),
            ("$.order_event", ORDER_EVENT),
            ("$.order_event.disposition_date", DATE),
            ("$.redaction", REDACTION),
            ("$.received_date", DATE),
            ("$.inspection_date", DATE),
            ("$.disposition_date", DATE),
        ],
    )
    ecb_shape = _shape(
        "payload",
        [
            ("$", ECB_RECORD),
            ("$.source_rows[*]", ECB_SOURCE_ROW),
            ("$.infractions[*]", INFRACTION),
            ("$.issue_date", DATE),
            ("$.served_date", DATE),
            ("$.hearing_date", DATE),
        ],
    )
    sr311_shape = _shape(
        "payload",
        [
            ("$", SR311_RECORD),
            ("$.redaction", REDACTION),
            ("$.received_date", DATE),
            ("$.closed_date", DATE),
            ("$.agency_updated_date", DATE),
        ],
    )
    op.execute(
        f"""
        ALTER TABLE records.normalized_record
        ADD CONSTRAINT task5_kind_dataset CHECK ({kind_dataset}),
        ADD CONSTRAINT task5_evidence_kind CHECK (
            (evidence_kind = 'enforcement_action') = (record_kind = '{ECB}')
            AND (record_kind NOT IN ('{DOB}', '{SR311}') OR evidence_kind = 'reported_allegation')
        ),
        ADD CONSTRAINT task5_attribution CHECK (
            (attribution_basis = 'bin_lot_mapping')
                = (record_kind = '{DOB}' AND attribution = 'other_building_on_lot')
            AND (lot_membership IS DISTINCT FROM 'via_bin_mapping'
                 OR record_kind IN ('{DOB}', '{ECB}'))
            AND (record_kind NOT IN ({T5_KINDS}) OR (
                original_attribution IS NULL AND mapped_bin IS NULL AND location_scope IS NULL))
            AND (record_kind <> '{DOB}' OR NOT {building} OR (
                lot_membership = 'via_bin_mapping'
                AND NOT quality_flags && ARRAY['bin_lot_conflict',
                                               'duplicate_id_different_content']))
            AND (record_kind <> '{ECB}' OR (
                attribution_basis = 'row_identifiers'
                AND (lot_membership IS DISTINCT FROM 'via_bin_mapping' OR (
                    attribution = 'this_building' AND 'lot_parts_missing' = ANY (quality_flags)))
                AND (NOT {building} OR (
                    lot_membership <> 'conflicting' AND NOT quality_flags && {ecb_blocking}))))
            AND (record_kind <> '{SR311}' OR (
                attribution IN ('unattributed_on_lot', 'lot_level')
                AND attribution_basis = 'row_identifiers'
                AND lot_membership IS DISTINCT FROM 'via_bin_mapping'))
        ),
        ADD CONSTRAINT task5_no_contact_strings CHECK (
            record_kind NOT IN ({T5_KINDS}) OR {_no_string("payload", CONTACT_LIKE)}
        ),
        ADD CONSTRAINT task5_orders_never_in_effect CHECK (
            NOT jsonb_path_exists(payload,
                'lax $.**.still_in_effect ? (@ != "not_shown_by_source")')
        ),
        ADD CONSTRAINT dob_record_shape CHECK (
            record_kind <> '{DOB}' OR (
                {dob_shape}
                AND coalesce(payload ->> 'complaint_number', '') <> ''
                AND (payload ->> 'outcome' = 'unknown_code')
                    = ('disposition_code_unknown' = ANY (quality_flags))
                AND (payload ->> 'outcome' <> 'unknown_code'
                     OR payload -> 'disposition_description' = 'null'::jsonb)
                AND (payload ->> 'outcome' <> 'order_event'
                     OR jsonb_typeof(payload -> 'order_event') = 'object')
            )
        ),
        ADD CONSTRAINT ecb_record_shape CHECK (
            record_kind <> '{ECB}' OR (
                {ecb_shape}
                AND payload ->> 'isn_dob_bis_extract' = source_record_id
                AND (payload ->> 'adjudication' <> 'unknown'
                     OR 'status_unknown' = ANY (quality_flags))
                AND (payload ->> 'severity' IN ('class_1', 'class_2', 'class_3'))
                    = ('severity_undocumented_value' = ANY (quality_flags))
                AND NOT jsonb_path_exists(payload,
                    'lax $.infractions[*] ? (@.law_description != null && @.code == null)')
            )
        ),
        ADD CONSTRAINT sr311_record_identity CHECK (
            record_kind <> '{SR311}' OR (
                {sr311_shape}
                AND source_record_id ~ {K311_PATTERN}
                AND payload ->> 'request_ref' = source_record_id
                AND payload ->> 'source_record_id' = source_record_id
                AND payload -> 'source_row_id' = 'null'::jsonb
                AND NOT (payload ? 'source_rows')
                AND jsonb_typeof(payload -> 'collapsed_copies') = 'number'
                AND (payload ->> 'collapsed_copies')::integer >= 1
                AND {_date_only("received_date")}
                AND {_date_only("closed_date")}
                AND {_date_only("agency_updated_date")}
                AND {_no_string("payload", SR311_ID_LIKE)}
            )
        )
        """
    )


def _fetch_checks() -> None:
    type_dataset = " AND ".join(
        f"((result_type = '{rt}') = (dataset_id = '{ds}'))" for rt, (ds, _) in T5_TYPES.items()
    )
    common = [("$.provenance", PROVENANCE)]
    dob_shape = _shape(
        "result_payload",
        [("$", DOB_RESULT), *common, ("$.bin_evidence_provenance[*]", (*PROVENANCE, "pages"))]
        + [("$.summary", DOB_SUMMARY), ("$.summary.order_events[*]", ORDER_EVENT)],
    )
    ecb_shape = _shape(
        "result_payload",
        [("$", ECB_RESULT), *common, ("$.bin_evidence_provenance[*]", (*PROVENANCE, "pages"))]
        + [("$.summary", ECB_SUMMARY)],
    )
    sr311_shape = _shape(
        "result_payload",
        [
            ("$", SR311_RESULT),
            *common,
            ("$.lot_family_provenance[*]", (*PROVENANCE, "pages")),
            ("$.summary", SR311_SUMMARY),
            ("$.excluded_counts", EXCLUDED),
            ("$.excluded_counts.by_agency", COUNT_CHECK),
            ("$.excluded_counts.null_agency", COUNT_CHECK),
            ("$.attempts[*]", ATTEMPT),
        ],
    )
    ex = "result_payload -> 'excluded_counts'"

    def established(which: str) -> str:
        return f"coalesce(({ex} #>> '{{{which},established}}')::boolean, false)"

    def gives_counts(which: str) -> str:
        return f"(coalesce(jsonb_typeof({ex} #> '{{{which},counts}}'), 'null') = 'object')"

    def any_string(path: str, test: str) -> str:
        return f"jsonb_path_exists(result_payload, 'lax {path} ? ({test})')"

    null_found = (
        f"({established('null_agency')} AND "
        f"coalesce(({ex} #>> '{{null_agency,counts,null_agency}}')::integer, 0) > 0)"
    )
    null_reason = '@ starts with "null_agency_rows_not_retrieved"'
    reasons = "$.provenance.incomplete_reasons[*]"
    has_null_reason = any_string(reasons, null_reason)
    has_other_reason = any_string(reasons, f"!({null_reason})")
    has_unknown_warning = any_string(
        "$.provenance.warnings[*]", '@ starts with "null_agency_coverage_unknown"'
    )
    last_accepted = any_string("$.attempts[last]", "@.accepted == true")
    accepted_with_reasons = any_string(
        "$.attempts[*]", "@.accepted == true && @.reasons.size() > 0"
    )
    accepted = "result_payload ->> 'accepted_data_version'"
    claims = (
        "coalesce((result_payload #>> '{summary,building_specific_claims_allowed}')::boolean, "
        "false)"
    )
    financials = "result_payload #> '{summary,requested_building_financials}'"
    in_effect = 'lax $.**.still_in_effect ? (@ != "not_shown_by_source")'
    op.execute(
        f"""
        ALTER TABLE records.source_fetch
        ADD CONSTRAINT task5_type_dataset CHECK ({type_dataset}),
        ADD CONSTRAINT task5_provenance_matches CHECK (
            result_type NOT IN ({T5_RESULT_TYPES}) OR (
                result_payload #> '{{provenance,incomplete_reasons}}'
                    = to_jsonb(incomplete_reasons)
                AND result_payload #> '{{provenance,warnings}}' = to_jsonb(warnings)
                AND (result_payload #>> '{{provenance,data_as_of}}')
                    IS NOT DISTINCT FROM data_as_of
                AND (result_payload #>> '{{provenance,where}}')
                    IS NOT DISTINCT FROM where_clause
                AND {_no_string("result_payload", CONTACT_LIKE)}
                AND NOT jsonb_path_exists(result_payload, '{in_effect}')
            )
        ),
        ADD CONSTRAINT dob_result_shape CHECK (
            result_type <> 'dob_complaints' OR ({dob_shape})
        ),
        ADD CONSTRAINT ecb_result_shape CHECK (
            result_type <> 'dob_ecb_violations' OR ({ecb_shape})
        ),
        ADD CONSTRAINT task5_building_claims CHECK (
            result_type NOT IN ('dob_complaints', 'dob_ecb_violations')
            OR ((NOT {claims} OR (
                    join_scope = 'building' AND resolved_bin IS NOT NULL
                    AND NOT coalesce(result_payload -> 'bin_lot_conflicts' ? resolved_bin, false)))
                AND (coalesce(jsonb_typeof({financials}), 'null') <> 'object' OR {claims}))
        ),
        ADD CONSTRAINT sr311_result_rules CHECK (
            result_type <> 'service_requests_311' OR (
                {sr311_shape}
                AND resolved_bin IS NULL
                AND NOT {claims}
                AND coalesce(where_clause, '') !~* '(:|%3a)id|unique_key'
                AND coalesce(order_by, '') !~* '(:|%3a)id'
                AND {_no_string("(result_payload - 'lot_family_provenance')", SR311_ID_LIKE)}
                AND (coalesce(jsonb_typeof(result_payload -> 'summary'), 'null') <> 'object'
                     OR (result_payload #>> '{{summary,complete}}')::boolean
                        = (cardinality(incomplete_reasons) = 0))
                AND (coalesce(jsonb_typeof({ex}), 'null') <> 'object' OR (
                    {established("by_agency")} = {gives_counts("by_agency")}
                    AND {established("null_agency")} = {gives_counts("null_agency")}
                    AND (NOT {established("by_agency")} OR (
                        status IN ('records_found', 'no_records_found')
                        AND cardinality(incomplete_reasons) = 0
                        AND {ex} #>> '{{by_agency,truth_header}}' = {accepted}))
                    AND (NOT {null_found} OR (
                        NOT {established("by_agency")} AND {has_null_reason}))
                    AND ({established("null_agency")} OR {has_unknown_warning})
                ))
                AND ({accepted} IS NULL OR NOT {has_other_reason})
                AND (status <> 'records_found' OR (
                    {accepted} IS NOT NULL
                    AND {accepted} IS NOT DISTINCT FROM data_as_of
                    AND {last_accepted}))
                AND NOT {accepted_with_reasons}
            )
        )
        """
    )


def _triggers() -> None:
    cases = "\n".join(f"WHEN '{rt}' THEN '{kind}'" for rt, (_, kind) in T5_TYPES.items())
    op.execute(
        f"""
        CREATE FUNCTION records.task5_record_kind() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE rt text; expected text;
        BEGIN
            SELECT result_type INTO rt FROM records.source_fetch WHERE id = NEW.fetch_id;
            expected := CASE rt {cases} END;
            IF rt IS NOT NULL AND NEW.record_kind IS DISTINCT FROM expected
               AND (expected IS NOT NULL OR NEW.record_kind IN ({T5_KINDS})) THEN
                RAISE EXCEPTION 'record kind does not match the fetch result type'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER kind_task5 BEFORE INSERT ON records.normalized_record "
        "FOR EACH ROW EXECUTE FUNCTION records.task5_record_kind()"
    )
    op.execute(
        f"""
        CREATE FUNCTION records.task5_page_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE rt text; ds text;
        BEGIN
            SELECT result_type, dataset_id INTO rt, ds
              FROM records.source_fetch WHERE id = NEW.fetch_id;
            IF rt IS NULL OR rt NOT IN ({T5_RESULT_TYPES}) THEN
                RETURN NEW;
            END IF;
            IF position('/resource/' || ds || '.json?' IN NEW.url) = 0 THEN
                RAISE EXCEPTION 'a page of this fetch must come from its own dataset'
                    USING ERRCODE = '23514';
            END IF;
            IF rt = 'service_requests_311' AND (
                NEW.url ~* '(:|%3a)id'
                OR NEW.url ~* 'unique_key( |[+]|%20)*(=|<|>|%3d|%3c|%3e|in[^a-z_])') THEN
                RAISE EXCEPTION 'a 311 page URL names a row id or a request key'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER task5_page_guard BEFORE INSERT ON records.fetch_page "
        "FOR EACH ROW EXECUTE FUNCTION records.task5_page_guard()"
    )


def _check_fetch_complete() -> str:
    """0006's check, with lineage compared by projection for DOB and ECB."""
    m6 = _sibling("0006_multi_row_lineage.py")
    middle = f"""
        -- Row-level lineage: the payload's source_rows are exactly the stored links.
        -- DOB and ECB entries carry extra fields; compare their (id, digest) projection.
        IF EXISTS (
            SELECT 1 FROM records.normalized_record r
            WHERE r.fetch_id = f.id AND r.payload ? 'source_rows'
              AND CASE WHEN r.record_kind IN ('{DOB}', '{ECB}') THEN (
                      SELECT jsonb_agg(jsonb_build_object(
                                 'source_row_id', e.v ->> 'source_row_id',
                                 'original_digest', e.v ->> 'original_digest') ORDER BY e.o)
                      FROM jsonb_array_elements(r.payload -> 'source_rows')
                           WITH ORDINALITY AS e (v, o))
                  ELSE r.payload -> 'source_rows' END
                  IS DISTINCT FROM (
                  SELECT jsonb_agg(jsonb_build_object(
                             'source_row_id', l.source_row_id,
                             'original_digest', l.original_digest) ORDER BY l.position)
                  FROM records.record_source_row l WHERE l.record_id = r.id)
        ) THEN
            RAISE EXCEPTION 'fetch %: a record''s source_rows differ from its stored links', f.id
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM records.normalized_record r
            WHERE r.fetch_id = f.id AND NOT (r.payload ? 'source_rows')
              AND (SELECT count(*) = 1 AND bool_and(l.source_row_id IS NULL)
                   FROM records.record_source_row l WHERE l.record_id = r.id) IS NOT TRUE
        ) THEN
            RAISE EXCEPTION 'fetch %: a single-row record must have exactly one plain link', f.id
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM records.record_source_row l
            JOIN records.normalized_record r ON r.id = l.record_id
            WHERE l.fetch_id = f.id
              AND l.source_record_id <> coalesce(l.source_row_id, r.source_record_id)
        ) THEN
            RAISE EXCEPTION 'fetch %: a link''s source id differs from its row', f.id
                USING ERRCODE = '23514';
        END IF;
        IF f.status = 'records_found' THEN
            IF (SELECT count(DISTINCT original_digest) FROM records.record_source_row
                WHERE fetch_id = f.id) <> n_links THEN
                RAISE EXCEPTION 'fetch %: a source row is linked more than once', f.id
                    USING ERRCODE = '23514';
            END IF;
            SELECT EXISTS (
                SELECT 1 FROM records.record_source_row
                WHERE fetch_id = f.id AND source_row_id IS NOT NULL
            ) INTO row_level;
            SELECT EXISTS (
                SELECT 1 FROM records.normalized_record
                WHERE fetch_id = f.id AND 'duplicate_id_same_content' = ANY (quality_flags)
            ) INTO collapsed;
            IF row_level THEN
                IF n_links <> f.source_count
                   OR (SELECT count(DISTINCT source_row_id) FROM records.record_source_row
                       WHERE fetch_id = f.id) <> n_links THEN
                    RAISE EXCEPTION
                        'fetch %: source rows do not account for the source count', f.id
                        USING ERRCODE = '23514';
                END IF;
            ELSIF (collapsed AND n_links >= f.source_count)
                  OR (NOT collapsed AND n_links <> f.source_count) THEN
                RAISE EXCEPTION 'fetch %: source rows do not account for the source count', f.id
                    USING ERRCODE = '23514';
            END IF;
        END IF;
"""
    return (
        m6._HEAD
        + "        row_level boolean;\n"
        + m6._COUNTS_AND_FIRST_LINK
        + middle
        + m6._BASIS_AND_END
    )


def _commit_checks() -> None:
    op.execute(
        """
        CREATE FUNCTION records.check_task5_fetch() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch; fin jsonb;
        BEGIN
            SELECT * INTO f FROM records.source_fetch WHERE id = NEW.id;
            IF f.result_type = 'service_requests_311' THEN
                IF EXISTS (
                    SELECT 1 FROM records.normalized_record
                    WHERE fetch_id = f.id
                      AND (((payload ->> 'collapsed_copies')::integer > 1)
                           <> ('duplicate_id_same_content' = ANY (quality_flags))
                           OR payload ->> 'join_scope' IS DISTINCT FROM f.join_scope)
                ) THEN
                    RAISE EXCEPTION 'fetch %: a 311 record''s copies or scope are inconsistent',
                        f.id USING ERRCODE = '23514';
                END IF;
                IF f.status = 'records_found' AND (
                    SELECT coalesce(sum((payload ->> 'collapsed_copies')::integer), 0)
                    FROM records.normalized_record WHERE fetch_id = f.id) <> f.source_count THEN
                    RAISE EXCEPTION 'fetch %: 311 copies do not add up to the source count', f.id
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF f.result_type = 'dob_ecb_violations' THEN
                fin := f.result_payload #> '{summary,requested_building_financials}';
                IF jsonb_typeof(fin) = 'object' AND (fin ->> 'summonses')::integer <> (
                    SELECT count(*) FROM records.normalized_record
                    WHERE fetch_id = f.id AND attribution = 'this_building') THEN
                    RAISE EXCEPTION
                        'fetch %: requested-building financials count other records', f.id
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NULL;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER task5_at_commit AFTER INSERT ON records.source_fetch "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION records.check_task5_fetch()"
    )


def downgrade() -> None:
    m6 = _sibling("0006_multi_row_lineage.py")
    m7 = _sibling("0007_admin_records.py")
    with as_owner():
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM records.source_fetch
                           WHERE result_type IN ({T5_RESULT_TYPES}))
                   OR EXISTS (SELECT 1 FROM records.normalized_record
                              WHERE record_kind IN ({T5_KINDS}))
                   OR EXISTS (SELECT 1 FROM evidence.raw_row
                              WHERE dataset_id IN ({T5_DATASETS})) THEN
                    RAISE EXCEPTION
                        'Task 5 rows exist; remove them before downgrading below 0011'
                        USING ERRCODE = '55000';
                END IF;
            END $$
            """
        )
        op.execute("DROP TRIGGER task5_at_commit ON records.source_fetch")
        op.execute("DROP FUNCTION records.check_task5_fetch()")
        op.execute(m6.NEW_CHECK)
        op.execute("DROP TRIGGER task5_page_guard ON records.fetch_page")
        op.execute("DROP FUNCTION records.task5_page_guard()")
        op.execute("DROP TRIGGER kind_task5 ON records.normalized_record")
        op.execute("DROP FUNCTION records.task5_record_kind()")
        op.execute("ALTER TABLE evidence.raw_row DROP CONSTRAINT sr311_raw_row_token")
        for c in (
            "sr311_result_rules",
            "task5_building_claims",
            "ecb_result_shape",
            "dob_result_shape",
            "task5_provenance_matches",
            "task5_type_dataset",
        ):
            op.execute(f"ALTER TABLE records.source_fetch DROP CONSTRAINT {c}")
        for c in (
            "sr311_record_identity",
            "ecb_record_shape",
            "dob_record_shape",
            "task5_orders_never_in_effect",
            "task5_no_contact_strings",
            "task5_attribution",
            "task5_evidence_kind",
            "task5_kind_dataset",
        ):
            op.execute(f"ALTER TABLE records.normalized_record DROP CONSTRAINT {c}")
        nr = "records.normalized_record"
        _replace_check(nr, "row_lineage_shape", _row_lineage_shape("'registration', 'contact'"))
        _replace_check(
            nr, "normalized_record_attribution_basis_check", f"attribution_basis IN ({OLD_BASES})"
        )
        _replace_check(
            nr,
            "normalized_record_lot_membership_check",
            f"lot_membership IN ({OLD_LOT_MEMBERSHIP})",
        )
        _replace_check(
            nr, "normalized_record_evidence_kind_check", f"evidence_kind IN ({OLD_EVIDENCE_KINDS})"
        )
        _replace_check(
            nr, "normalized_record_record_kind_check", f"record_kind IN ({OLD_RECORD_KINDS})"
        )
        _replace_check(
            "records.source_fetch",
            "source_fetch_result_type_check",
            f"result_type IN ({OLD_RESULT_TYPES})",
        )
        _replace_check(
            "records.source_fetch", "unsupported_join_made_no_requests", UNSUPPORTED_0004
        )
        op.execute(m7._forbidden_keys_fn(m7.OLD_FORBIDDEN + m7.CONTACT_PRIVATE))
