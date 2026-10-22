"""HPD administrative records: buildings, registrations, contacts (Task 4, step 5).

Additive on top of 0006. Building records, registrations and registration contacts are
stored as normalized_record rows (record_kind hpd_building, registration, contact) under
their own fetches. Registrations and contacts carry row-level lineage (source_rows, one
link per Socrata :id, checked at commit by 0006).

Database-enforced:
  - Kinds: each admin result type holds only its own record kind, and vice versa.
    Administrative records are evidence_kind administrative_record and nothing else is.
  - Attribution basis: building records and registrations are attributed by their own
    row identifiers. Contacts have no location of their own: they are attributed through
    their registration's buildings, recorded as attribution_basis = registration_coverage.
    No original attribution, mapped BIN or location scope on any admin record.
  - Contacts have no lot membership and no resolved_bbl in their payload (the column is
    the fetch's lot); buildings and registrations have both.
  - Identity: a building record's source_record_id is its HPD building ID; a
    registration's or contact's is its own Socrata :id, the first of its source_rows.
  - Building records: is_current follows lifecycle Building + record status Active.
  - Registrations: the shared_registration flag exactly when the registration covers
    more than one building on the lot; rows of one registration agree on that count.
    Partial fetches cannot state a requested-building registration status, and status
    labels are only the six approved ones (never "expired" or "unregistered").
  - Contacts: a contact naming a person publishes no city, state or zip; the role is
    exactly HPD's registered type mapped one to one (unknown types stay 'other'); the
    association follows the record's attribution and registration_shared.
  - Private keys: person-name and business street/suite keys are refused anywhere in
    any public JSONB value (records.forbidden_keys_absent is extended).
  - records.registration_coverage: one row per registration ID a contacts fetch looked
    up: the buildings on this lot, every building citywide with that registration (or
    NULL when that lookup did not complete) and the derived association. CHECKs tie
    shared / unknown / requested-only to the coverage; at commit every contact must
    agree with its registration's row, counts must match, and the coverage status must
    match which rows have coverage.
  - Link identity: at commit every source-row link's source id must be the source id its
    raw row was stored under, in the record's dataset, so a Socrata :id can never be
    paired with another row's digest. Checked through records.evidence_identity
    (digest, dataset, source id; no row content), readable by ingest_writer only, because
    for future 311 rows the source id is the request number, never public by default.
  - BuildingID attribution (D9): source_fetch.buildings_fetch_id names the stored HPD
    building records a violations or complaints result was attributed with (same run).
    An applied report requires it; the linked fetch must be what the report says it
    used. Every record upgraded by BuildingID must map, in that fetch, to exactly one
    current, confirmed-lot building record with that building ID and the mapped BIN.
    At commit the report's before/after/upgraded counts must equal the stored records.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-22
"""

# ruff: noqa: S608  (SQL assembled from constant fragments only; no input)

import json

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ATTRIBUTIONS = "'this_building', 'other_building_on_lot', 'unattributed_on_lot', 'lot_level'"
ASSOCIATIONS = (
    "'requested_building_only', 'shared_including_requested_building', "
    "'requested_building_sharing_unknown', 'other_buildings_only', 'lot_level', 'unattributed'"
)
ADMIN_KINDS = "'hpd_building', 'registration', 'contact'"
REGISTRATION_STATUSES = (
    "on_file_end_date_not_passed",
    "on_file_end_date_passed_after_data_update",
    "on_file_end_date_passed_before_data_update",
    "on_file_end_date_passed_freshness_unknown",
    "on_file_end_date_unknown",
    "none_on_file",
)
# HPD contact type (spaces removed, lower case) -> stored role. Mirrors hpd_contacts.ROLES.
ROLE_MAP = {
    "corporateowner": "corporate_owner",
    "individualowner": "individual_owner",
    "jointowner": "joint_owner",
    "headofficer": "head_officer",
    "officer": "officer",
    "shareholder": "shareholder",
    "agent": "agent",
    "sitemanager": "site_manager",
    "lessee": "lessee",
}
OLD_FORBIDDEN = ("apartment", "story", "complaint_anonymous_flag", "unique_key", "input")
CONTACT_PRIVATE = (
    "firstname",
    "middleinitial",
    "lastname",
    "title",
    "businesshousenumber",
    "businessstreetname",
    "businessapartment",
    "businesscity",
    "businessstate",
    "businesszip",
)


def _forbidden_keys_fn(keys) -> str:
    tests = "\n                OR ".join(f"jsonb_path_exists(j, 'lax $.**.{k}')" for k in keys)
    return f"""
        CREATE OR REPLACE FUNCTION records.forbidden_keys_absent(j jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT NOT (
                {tests}
            )
        $fn$
    """


def _association(attribution: str, shared: str) -> str:
    """The association a registration's coverage implies (hpd_contacts.associations)."""
    return f"""CASE {attribution}
                WHEN 'this_building' THEN CASE
                    WHEN {shared} THEN 'shared_including_requested_building'
                    WHEN NOT {shared} THEN 'requested_building_only'
                    ELSE 'requested_building_sharing_unknown' END
                WHEN 'other_building_on_lot' THEN 'other_buildings_only'
                WHEN 'lot_level' THEN 'lot_level'
                ELSE 'unattributed' END"""


def _status_filter(path: str) -> str:
    allowed = " || ".join(f'@ == "{s}"' for s in REGISTRATION_STATUSES)
    return f"NOT jsonb_path_exists(result_payload, 'lax {path} ? (!({allowed}))')"


def upgrade() -> None:
    with as_owner():
        op.execute(_forbidden_keys_fn(OLD_FORBIDDEN + CONTACT_PRIVATE))
        _record_checks()
        _coverage_table()
        _fetch_columns_and_checks()
        _triggers()
        _link_identity()


def _link_identity() -> None:
    op.execute(
        """
        CREATE VIEW records.evidence_identity AS
        SELECT original_digest, dataset_id, source_record_id FROM evidence.raw_row
        """
    )
    op.execute("REVOKE ALL ON records.evidence_identity FROM PUBLIC, app_reader, ingest_writer")
    op.execute("GRANT SELECT ON records.evidence_identity TO ingest_writer")
    op.execute(
        """
        CREATE FUNCTION records.check_link_identity() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM records.record_source_row l
                JOIN records.normalized_record r ON r.id = l.record_id
                JOIN records.evidence_identity e ON e.original_digest = l.original_digest
                WHERE l.fetch_id = NEW.id
                  AND (e.dataset_id <> r.dataset_id OR e.source_record_id <> l.source_record_id)
            ) THEN
                RAISE EXCEPTION 'fetch %: a link''s source id is not its raw row''s', NEW.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER link_identity_at_commit AFTER INSERT ON "
        "records.source_fetch DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION records.check_link_identity()"
    )


def _record_checks() -> None:
    op.execute(
        "ALTER TABLE records.normalized_record "
        "DROP CONSTRAINT normalized_record_attribution_basis_check"
    )
    op.execute(
        "ALTER TABLE records.normalized_record ADD CONSTRAINT "
        "normalized_record_attribution_basis_check CHECK (attribution_basis IN "
        "('row_identifiers', 'hpd_building_id', 'registration_coverage'))"
    )
    role_map = json.dumps(ROLE_MAP)
    op.execute(
        f"""
        ALTER TABLE records.normalized_record
        ADD CONSTRAINT flags_match_payload CHECK (
            CASE WHEN payload ? 'quality_flags'
                 THEN payload -> 'quality_flags' = to_jsonb(quality_flags)
                 ELSE record_kind = 'contact' AND cardinality(quality_flags) = 0 END
        ),
        ADD CONSTRAINT admin_record_shape CHECK (
            (record_kind IN ({ADMIN_KINDS})) = (evidence_kind = 'administrative_record')
            AND (attribution_basis = 'registration_coverage') = (record_kind = 'contact')
            AND (record_kind NOT IN ({ADMIN_KINDS}) OR (
                attribution_basis IN ('row_identifiers', 'registration_coverage')
                AND original_attribution IS NULL AND mapped_bin IS NULL
                AND location_scope IS NULL
                AND NOT (payload ?| ARRAY['attribution_basis', 'original_attribution',
                                          'mapped_bin', 'location_scope'])
                AND (record_kind = 'contact') = (lot_membership IS NULL)
                AND (record_kind = 'contact') = (NOT (payload ? 'resolved_bbl'))
                AND (record_kind = 'contact') = (NOT (payload ? 'lot_membership'))
                AND payload ->> CASE record_kind WHEN 'hpd_building' THEN 'building_id'
                                                 ELSE 'source_row_id' END
                    IS NOT DISTINCT FROM source_record_id
            ))
        ),
        ADD CONSTRAINT row_lineage_shape CHECK (
            (record_kind IN ('registration', 'contact')) = (payload ? 'source_rows')
            AND CASE WHEN payload ? 'source_rows' THEN
                    jsonb_typeof(payload -> 'source_rows') = 'array'
                    AND payload #>> '{{source_rows,0,source_row_id}}'
                        IS NOT DISTINCT FROM source_record_id
                    AND payload #>> '{{source_rows,0,original_digest}}'
                        IS NOT DISTINCT FROM original_digest
                ELSE true END
        ),
        ADD CONSTRAINT building_current_follows_lifecycle CHECK (
            record_kind <> 'hpd_building' OR (
                jsonb_typeof(payload -> 'is_current') = 'boolean'
                AND (payload ->> 'is_current')::boolean = coalesce(
                    lower(payload ->> 'lifecycle') = 'building'
                    AND lower(payload ->> 'record_status') = 'active', false)
            )
        ),
        ADD CONSTRAINT registration_sharing_flag CHECK (
            record_kind <> 'registration' OR (
                jsonb_typeof(payload -> 'buildings_on_lot_with_this_registration') = 'number'
                AND (payload ->> 'buildings_on_lot_with_this_registration')::integer >= 1
                AND (payload -> 'quality_flags' ? 'shared_registration')
                    = ((payload ->> 'buildings_on_lot_with_this_registration')::integer > 1)
            )
        ),
        ADD CONSTRAINT contact_person_has_no_address CHECK (
            record_kind <> 'contact' OR (
                jsonb_typeof(payload -> 'names_a_person') = 'boolean'
                AND payload ?& ARRAY['public_city', 'public_state', 'public_zip']
                AND (NOT (payload ->> 'names_a_person')::boolean OR (
                    payload -> 'public_city' = 'null'::jsonb
                    AND payload -> 'public_state' = 'null'::jsonb
                    AND payload -> 'public_zip' = 'null'::jsonb))
            )
        ),
        ADD CONSTRAINT contact_role_is_hpd_type CHECK (
            record_kind <> 'contact' OR (
                payload ? 'role_raw'
                AND payload ->> 'role' = coalesce(
                    '{role_map}'::jsonb
                        ->> lower(replace(coalesce(payload ->> 'role_raw', ''), ' ', '')),
                    'other')
            )
        ),
        ADD CONSTRAINT contact_association_follows_coverage CHECK (
            record_kind <> 'contact' OR (
                payload ? 'registration_shared'
                AND payload ->> 'association' = {
            _association("attribution", "(payload ->> 'registration_shared')::boolean")
        }
            )
        )
        """
    )


def _coverage_table() -> None:
    op.execute(
        f"""
        CREATE TABLE records.registration_coverage (
            id                         uuid PRIMARY KEY,
            fetch_id                   uuid NOT NULL REFERENCES records.source_fetch (id),
            ordinal                    integer NOT NULL CHECK (ordinal >= 0),
            registration_id            text NOT NULL CHECK (registration_id ~ '^[0-9]+$'),
            attribution                text NOT NULL CHECK (attribution IN ({ATTRIBUTIONS})),
            association                text NOT NULL CHECK (association IN ({ASSOCIATIONS})),
            shared_registration        boolean,
            building_ids_on_lot        text[] NOT NULL,
            covered_building_ids       text[],
            covered_buildings_off_lot  integer CHECK (covered_buildings_off_lot >= 0),
            contacts                   integer NOT NULL CHECK (contacts >= 0),
            persons_named_withheld     integer NOT NULL CHECK (persons_named_withheld >= 0),
            payload                    jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            schema_version             integer NOT NULL CHECK (schema_version >= 1),
            UNIQUE (fetch_id, registration_id),
            UNIQUE (fetch_id, ordinal),
            CONSTRAINT association_follows_coverage CHECK (
                association = {_association("attribution", "shared_registration")}
            ),
            CONSTRAINT shared_follows_coverage CHECK (
                shared_registration IS NOT DISTINCT FROM CASE
                    WHEN cardinality(building_ids_on_lot) > 1
                         OR coalesce(cardinality(covered_building_ids), 0) > 1 THEN true
                    WHEN covered_building_ids IS NOT NULL THEN false
                END
            ),
            CONSTRAINT coverage_includes_lot CHECK (
                covered_building_ids IS NULL OR covered_building_ids @> building_ids_on_lot
            ),
            CONSTRAINT off_lot_count_follows_coverage CHECK (
                covered_buildings_off_lot IS NOT DISTINCT FROM
                    cardinality(covered_building_ids) - cardinality(building_ids_on_lot)
            ),
            CONSTRAINT persons_within_contacts CHECK (persons_named_withheld <= contacts),
            CONSTRAINT payload_matches CHECK (
                payload ?& ARRAY['registration_id', 'attribution', 'association',
                                 'shared_registration', 'building_ids_on_lot',
                                 'covered_building_ids', 'covered_buildings_off_lot',
                                 'contacts', 'persons_named_withheld']
                AND payload ->> 'registration_id' = registration_id
                AND payload ->> 'attribution' = attribution
                AND payload ->> 'association' = association
                AND (payload ->> 'shared_registration')::boolean
                    IS NOT DISTINCT FROM shared_registration
                AND payload -> 'building_ids_on_lot' = to_jsonb(building_ids_on_lot)
                AND payload -> 'covered_building_ids'
                    = coalesce(to_jsonb(covered_building_ids), 'null'::jsonb)
                AND (payload ->> 'covered_buildings_off_lot')::integer
                    IS NOT DISTINCT FROM covered_buildings_off_lot
                AND (payload ->> 'contacts')::integer = contacts
                AND (payload ->> 'persons_named_withheld')::integer = persons_named_withheld
            ),
            CONSTRAINT payload_has_no_private_keys CHECK (records.forbidden_keys_absent(payload))
        )
        """
    )
    op.execute(
        "CREATE INDEX registration_coverage_fetch ON records.registration_coverage (fetch_id)"
    )
    op.execute("GRANT SELECT ON records.registration_coverage TO app_reader")
    op.execute("GRANT SELECT, INSERT ON records.registration_coverage TO ingest_writer")


def _fetch_columns_and_checks() -> None:
    op.execute(
        """
        ALTER TABLE records.source_fetch
        ADD COLUMN coverage_count integer NOT NULL DEFAULT 0 CHECK (coverage_count >= 0),
        ADD COLUMN buildings_fetch_id uuid,
        ADD CONSTRAINT buildings_fetch_same_run FOREIGN KEY (buildings_fetch_id, run_id)
            REFERENCES records.source_fetch (id, run_id)
        """
    )
    op.execute("ALTER TABLE records.source_fetch ALTER COLUMN coverage_count DROP DEFAULT")
    op.execute(
        f"""
        ALTER TABLE records.source_fetch
        ADD CONSTRAINT coverage_only_for_contacts CHECK (
            (coverage_count = 0 OR result_type = 'hpd_contacts')
            AND NOT coalesce(result_payload -> 'summary' ? 'by_registration', false)
            AND coalesce(result_payload #>> '{{summary,coverage_status}}', 'complete')
                IN ('complete', 'incomplete', 'unavailable', 'not_needed')
        ),
        ADD CONSTRAINT registration_status_labels CHECK (
            result_type <> 'hpd_registrations' OR (
                {_status_filter("$.summary.buildings[*].status")}
                AND {_status_filter("$.summary.requested_building.status")}
                AND (status <> 'incomplete_pagination'
                     OR jsonb_typeof(result_payload #> '{{summary,requested_building}}')
                        IS DISTINCT FROM 'object')
            )
        ),
        ADD CONSTRAINT building_id_report_matches CHECK (
            building_id_applied = coalesce(
                (result_payload #>> '{{building_id_attribution,applied}}')::boolean, false)
        ),
        ADD CONSTRAINT building_id_needs_its_buildings CHECK (
            (NOT building_id_applied OR buildings_fetch_id IS NOT NULL)
            AND (buildings_fetch_id IS NULL OR (
                result_type IN ('hpd_violations', 'hpd_complaints')
                AND jsonb_typeof(result_payload -> 'building_id_attribution') = 'object'))
        )
        """
    )


def _triggers() -> None:
    op.execute(
        f"""
        CREATE FUNCTION records.admin_record_kind() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE rt text; expected text;
        BEGIN
            SELECT result_type INTO rt FROM records.source_fetch WHERE id = NEW.fetch_id;
            expected := CASE rt WHEN 'hpd_buildings' THEN 'hpd_building'
                                WHEN 'hpd_registrations' THEN 'registration'
                                WHEN 'hpd_contacts' THEN 'contact' END;
            IF rt IS NOT NULL AND NEW.record_kind IS DISTINCT FROM expected
               AND (expected IS NOT NULL OR NEW.record_kind IN ({ADMIN_KINDS})) THEN
                RAISE EXCEPTION 'record kind does not match the fetch result type'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER kind_admin BEFORE INSERT ON records.normalized_record "
        "FOR EACH ROW EXECUTE FUNCTION records.admin_record_kind()"
    )

    op.execute(
        """
        CREATE FUNCTION records.registration_coverage_before_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch;
        BEGIN
            f := records.open_fetch(NEW.fetch_id);
            IF f.id IS NULL THEN
                RETURN NEW;
            END IF;
            IF f.result_type <> 'hpd_contacts' THEN
                RAISE EXCEPTION 'registration coverage belongs only to a contacts fetch'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.attribution IN ('this_building', 'other_building_on_lot')
               AND f.join_scope IS DISTINCT FROM 'building' THEN
                RAISE EXCEPTION 'building-level attribution requires a building-scope fetch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER before_insert BEFORE INSERT ON records.registration_coverage "
        "FOR EACH ROW EXECUTE FUNCTION records.registration_coverage_before_insert()"
    )

    op.execute(
        """
        CREATE FUNCTION records.building_id_source() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE b records.source_fetch; rep jsonb;
        BEGIN
            IF NEW.buildings_fetch_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT * INTO b FROM records.source_fetch WHERE id = NEW.buildings_fetch_id;
            IF b.id IS NULL THEN
                RETURN NEW;  -- the foreign key reports it
            END IF;
            rep := NEW.result_payload -> 'building_id_attribution';
            IF b.result_type <> 'hpd_buildings'
               OR b.dataset_id IS DISTINCT FROM rep ->> 'buildings_dataset_id'
               OR b.status IS DISTINCT FROM rep ->> 'buildings_status'
               OR b.data_as_of IS DISTINCT FROM rep ->> 'buildings_data_as_of' THEN
                RAISE EXCEPTION 'BuildingID report does not describe the linked building records'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.building_id_applied AND (
                b.status <> 'records_found' OR b.join_scope IS DISTINCT FROM 'building'
                OR NEW.join_scope IS DISTINCT FROM 'building'
                OR b.resolved_bbl IS DISTINCT FROM NEW.resolved_bbl
                OR b.resolved_bin IS DISTINCT FROM NEW.resolved_bin) THEN
                RAISE EXCEPTION
                    'BuildingID attribution needs complete building records for the same building'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER building_id_source BEFORE INSERT ON records.source_fetch "
        "FOR EACH ROW EXECUTE FUNCTION records.building_id_source()"
    )

    op.execute(
        """
        CREATE FUNCTION records.building_id_evidence() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch; n integer; ok integer;
        BEGIN
            IF NEW.attribution_basis <> 'hpd_building_id' THEN
                RETURN NEW;
            END IF;
            SELECT * INTO f FROM records.source_fetch WHERE id = NEW.fetch_id;
            IF f.id IS NULL THEN
                RETURN NEW;
            END IF;
            IF NOT f.building_id_applied OR f.buildings_fetch_id IS NULL THEN
                RAISE EXCEPTION 'BuildingID attribution needs an applied report and its records'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*),
                   count(*) FILTER (
                       WHERE b.lot_membership = 'confirmed'
                         AND (b.payload ->> 'is_current')::boolean
                         AND b.payload ->> 'source_bin' = NEW.mapped_bin
                         AND NOT b.quality_flags
                             && ARRAY['multiple_records_for_bin', 'building_record_mismatch'])
              INTO n, ok
              FROM records.normalized_record b
             WHERE b.fetch_id = f.buildings_fetch_id
               AND b.source_record_id = NEW.payload ->> 'building_id';
            IF n <> 1 OR ok <> 1 THEN
                RAISE EXCEPTION
                    'BuildingID does not map to one current building record with that BIN'
                    USING ERRCODE = '23514';
            END IF;
            IF (NEW.mapped_bin = f.resolved_bin) <> (NEW.attribution = 'this_building') THEN
                RAISE EXCEPTION 'BuildingID attribution does not follow the mapped BIN'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER building_id_evidence BEFORE INSERT ON records.normalized_record "
        "FOR EACH ROW EXECUTE FUNCTION records.building_id_evidence()"
    )

    op.execute(
        """
        CREATE FUNCTION records.check_building_id_report() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch; rep jsonb;
        BEGIN
            SELECT * INTO f FROM records.source_fetch WHERE id = NEW.id;
            rep := f.result_payload -> 'building_id_attribution';
            IF jsonb_typeof(rep) IS DISTINCT FROM 'object' THEN
                RETURN NULL;
            END IF;
            IF rep -> 'after' IS DISTINCT FROM (
                   SELECT coalesce(jsonb_object_agg(a, n), '{}') FROM (
                       SELECT attribution AS a, count(*) AS n FROM records.normalized_record
                       WHERE fetch_id = f.id GROUP BY 1) x)
               OR rep -> 'before' IS DISTINCT FROM (
                   SELECT coalesce(jsonb_object_agg(a, n), '{}') FROM (
                       SELECT coalesce(original_attribution, attribution) AS a, count(*) AS n
                       FROM records.normalized_record WHERE fetch_id = f.id GROUP BY 1) x)
               OR coalesce(rep -> 'upgraded', '{}') IS DISTINCT FROM (
                   SELECT coalesce(jsonb_object_agg(a, n), '{}') FROM (
                       SELECT attribution AS a, count(*) AS n FROM records.normalized_record
                       WHERE fetch_id = f.id AND attribution_basis = 'hpd_building_id'
                       GROUP BY 1) x)
               OR (jsonb_typeof(rep -> 'complaints_after') = 'object'
                   AND rep -> 'complaints_after' IS DISTINCT FROM (
                       SELECT coalesce(jsonb_object_agg(a, n), '{}') FROM (
                           SELECT attribution AS a, count(*) AS n FROM records.complaint_group
                           WHERE fetch_id = f.id GROUP BY 1) x)) THEN
                RAISE EXCEPTION 'fetch %: BuildingID report counts differ from its records', f.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER building_id_report_at_commit AFTER INSERT ON "
        "records.source_fetch DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION records.check_building_id_report()"
    )

    op.execute(
        """
        CREATE FUNCTION records.check_admin_records() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch; cov_status text; req jsonb;
        BEGIN
            SELECT * INTO f FROM records.source_fetch WHERE id = NEW.id;
            IF f.result_type = 'hpd_registrations' THEN
                IF EXISTS (
                    SELECT 1 FROM records.normalized_record WHERE fetch_id = f.id
                    GROUP BY payload ->> 'registration_id'
                    HAVING count(DISTINCT payload ->> 'buildings_on_lot_with_this_registration')
                           > 1
                        OR min((payload ->> 'buildings_on_lot_with_this_registration')::integer)
                           < count(DISTINCT coalesce(payload ->> 'building_id', ''))
                ) THEN
                    RAISE EXCEPTION
                        'fetch %: shared-registration counts do not match its rows', f.id
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF f.result_type <> 'hpd_contacts' THEN
                RETURN NULL;
            END IF;
            IF (SELECT count(*) FROM records.registration_coverage WHERE fetch_id = f.id)
               <> f.coverage_count THEN
                RAISE EXCEPTION 'fetch %: stored coverage count does not match its rows', f.id
                    USING ERRCODE = '23514';
            END IF;
            IF jsonb_typeof(f.result_payload -> 'summary') IS DISTINCT FROM 'object'
               AND f.coverage_count > 0 THEN
                RAISE EXCEPTION 'fetch %: coverage rows without a summary', f.id
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM records.normalized_record r
                LEFT JOIN records.registration_coverage c
                  ON c.fetch_id = f.id AND c.registration_id = r.payload ->> 'registration_id'
                WHERE r.fetch_id = f.id AND (
                    c.id IS NULL OR c.attribution <> r.attribution
                    OR c.association <> r.payload ->> 'association'
                    OR c.shared_registration
                       IS DISTINCT FROM (r.payload ->> 'registration_shared')::boolean)
            ) THEN
                RAISE EXCEPTION
                    'fetch %: a contact disagrees with its registration''s coverage', f.id
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM records.registration_coverage c
                CROSS JOIN LATERAL (
                    SELECT count(*) AS n,
                           count(*) FILTER (WHERE (r.payload ->> 'names_a_person')::boolean)
                               AS persons
                    FROM records.normalized_record r
                    WHERE r.fetch_id = f.id AND r.payload ->> 'registration_id' = c.registration_id
                ) actual
                WHERE c.fetch_id = f.id
                  AND (c.contacts <> actual.n OR c.persons_named_withheld <> actual.persons)
            ) THEN
                RAISE EXCEPTION 'fetch %: coverage contact counts do not match its records', f.id
                    USING ERRCODE = '23514';
            END IF;
            cov_status := f.result_payload #>> '{summary,coverage_status}';
            IF (cov_status = 'unavailable' AND EXISTS (
                    SELECT 1 FROM records.registration_coverage
                    WHERE fetch_id = f.id AND covered_building_ids IS NOT NULL))
               OR (cov_status = 'complete' AND EXISTS (
                    SELECT 1 FROM records.registration_coverage
                    WHERE fetch_id = f.id AND covered_building_ids IS NULL))
               OR (cov_status = 'not_needed' AND f.record_count > 0) THEN
                RAISE EXCEPTION 'fetch %: coverage status does not match its rows', f.id
                    USING ERRCODE = '23514';
            END IF;
            req := f.result_payload #> '{summary,requested_building}';
            IF jsonb_typeof(req) = 'array' AND req IS DISTINCT FROM (
                SELECT coalesce(jsonb_agg(payload ORDER BY ordinal), '[]')
                FROM records.registration_coverage
                WHERE fetch_id = f.id AND attribution = 'this_building'
            ) THEN
                RAISE EXCEPTION
                    'fetch %: requested-building registrations differ from coverage', f.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER admin_records_at_commit AFTER INSERT ON "
        "records.source_fetch DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION records.check_admin_records()"
    )


def downgrade() -> None:
    with as_owner():
        op.execute("DROP TRIGGER link_identity_at_commit ON records.source_fetch")
        op.execute("DROP FUNCTION records.check_link_identity()")
        op.execute("DROP VIEW records.evidence_identity")
        op.execute("DROP TRIGGER admin_records_at_commit ON records.source_fetch")
        op.execute("DROP FUNCTION records.check_admin_records()")
        op.execute("DROP TRIGGER building_id_report_at_commit ON records.source_fetch")
        op.execute("DROP FUNCTION records.check_building_id_report()")
        op.execute("DROP TRIGGER building_id_evidence ON records.normalized_record")
        op.execute("DROP FUNCTION records.building_id_evidence()")
        op.execute("DROP TRIGGER building_id_source ON records.source_fetch")
        op.execute("DROP FUNCTION records.building_id_source()")
        op.execute("DROP TRIGGER kind_admin ON records.normalized_record")
        op.execute("DROP FUNCTION records.admin_record_kind()")
        for c in (
            "building_id_needs_its_buildings",
            "building_id_report_matches",
            "registration_status_labels",
            "coverage_only_for_contacts",
            "buildings_fetch_same_run",
        ):
            op.execute(f"ALTER TABLE records.source_fetch DROP CONSTRAINT {c}")
        op.execute("ALTER TABLE records.source_fetch DROP COLUMN buildings_fetch_id")
        op.execute("ALTER TABLE records.source_fetch DROP COLUMN coverage_count")
        op.execute("DROP TABLE records.registration_coverage")
        op.execute("DROP FUNCTION records.registration_coverage_before_insert()")
        for c in (
            "contact_association_follows_coverage",
            "contact_role_is_hpd_type",
            "contact_person_has_no_address",
            "registration_sharing_flag",
            "building_current_follows_lifecycle",
            "row_lineage_shape",
            "admin_record_shape",
            "flags_match_payload",
            "normalized_record_attribution_basis_check",
        ):
            op.execute(f"ALTER TABLE records.normalized_record DROP CONSTRAINT {c}")
        op.execute(
            "ALTER TABLE records.normalized_record ADD CONSTRAINT "
            "normalized_record_attribution_basis_check CHECK (attribution_basis IN "
            "('row_identifiers', 'hpd_building_id'))"
        )
        op.execute(_forbidden_keys_fn(OLD_FORBIDDEN))
