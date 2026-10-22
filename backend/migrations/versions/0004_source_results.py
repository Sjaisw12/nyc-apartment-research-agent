"""Public source-result snapshots (Task 4, step 3): runs, fetches, pages, records, links.

All objects are created as nyc_owner, so 0001's default privileges apply:
app_reader SELECT; ingest_writer SELECT and INSERT; nobody else has UPDATE or DELETE.
Snapshots are therefore append-only for every application role.

Tables
  records.retrieval_run      one research request. Stores an HMAC of the normalized
                             address input (input_digest), never the input itself, and a
                             whitelisted projection of the resolver result.
  records.source_fetch       one source result (violations in step 3). Typed columns for
                             status, scope, counts and provenance; result_payload holds
                             the public result model without its records and pages.
  records.fetch_page         one HTTP response of that fetch (count call is index -1).
  records.normalized_record  one public record; payload is the full public model.
  records.record_source_row  lineage: record -> raw row (evidence.raw_row) by digest.

Invariants enforced here (Pydantic checks the rest on save and again on load):
  - CHECKs: enumerations, identifier and digest formats, status/completeness rules,
    D9 basis rules, typed columns equal to the payload's own values, forbidden keys
    (apartment, story, complaint_anonymous_flag, unique_key, input) absent from every
    JSONB value, resolver projection limited to whitelisted keys.
  - Foreign keys: run -> fetch -> page/record; (record, fetch) -> link; link -> raw row.
  - Row triggers: a fetch at building scope needs a building-level run and matching
    identifiers; records only under records_found / incomplete_pagination; building
    attributions only at building scope; nothing may be added to a fetch committed by an
    earlier transaction (sealed snapshot).
  - Deferred constraint trigger (at commit): stored counts equal the rows present; every
    record's position-0 link is its own digest; links equal source_count on a complete
    fetch (fewer only when identical duplicates were collapsed); every link's raw row has
    the record's digest basis.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

HEX64 = "'^[0-9a-f]{64}$'"
BBL = "'^[1-5][0-9]{9}$'"
BIN = "'^[1-5][0-9]{6}$'"
DATASET = "'^[a-z0-9]{4}-[a-z0-9]{4}$'"
SOCRATA_URL = r"'^https://data\.cityofnewyork\.us/resource/[a-z0-9]{4}-[a-z0-9]{4}\.json\?'"

STATUSES = (
    "'records_found', 'no_records_found', 'source_unavailable', "
    "'incomplete_pagination', 'unsupported_join'"
)
ATTRIBUTIONS = "'this_building', 'other_building_on_lot', 'unattributed_on_lot', 'lot_level'"
RESULT_TYPES = (
    "'hpd_violations', 'hpd_complaints', 'hpd_buildings', 'hpd_registrations', 'hpd_contacts'"
)
RECORD_KINDS = "'violation', 'complaint_problem', 'hpd_building', 'registration', 'contact'"


def upgrade() -> None:
    with as_owner():
        _helpers()
        _tables()
        _triggers()
        # CHECK expressions and trigger bodies call these with the inserting role's own
        # rights, so the writer needs EXECUTE. They read nothing beyond their arguments,
        # except open_fetch, which reads records.source_fetch (already readable).
        op.execute(
            "GRANT EXECUTE ON FUNCTION records.forbidden_keys_absent(jsonb), "
            "records.keys_within(jsonb, text[]), records.resolution_projection_ok(jsonb), "
            "records.open_fetch(uuid) TO ingest_writer"
        )


def _helpers() -> None:
    op.execute(
        """
        CREATE FUNCTION records.forbidden_keys_absent(j jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT NOT (
                jsonb_path_exists(j, 'lax $.**.apartment')
                OR jsonb_path_exists(j, 'lax $.**.story')
                OR jsonb_path_exists(j, 'lax $.**.complaint_anonymous_flag')
                OR jsonb_path_exists(j, 'lax $.**.unique_key')
                OR jsonb_path_exists(j, 'lax $.**.input')
            )
        $fn$
        """
    )
    op.execute(
        """
        CREATE FUNCTION records.keys_within(j jsonb, allowed text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT CASE
                WHEN j IS NULL OR jsonb_typeof(j) = 'null' THEN true
                WHEN jsonb_typeof(j) <> 'object' THEN false
                ELSE NOT EXISTS (SELECT 1 FROM jsonb_object_keys(j) k WHERE k <> ALL (allowed))
            END
        $fn$
        """
    )
    op.execute(
        """
        CREATE FUNCTION records.resolution_projection_ok(j jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT records.keys_within(j, ARRAY['outcome', 'resolution_level', 'reasons',
                       'building', 'options', 'candidate_bbls', 'candidate_bins'])
               AND records.keys_within(j -> 'building',
                       ARRAY['bbl', 'bin', 'bin_evidence', 'borough', 'borough_source'])
               AND NOT EXISTS (
                   SELECT 1 FROM jsonb_array_elements(coalesce(j -> 'options', '[]')) o
                   WHERE NOT records.keys_within(o, ARRAY['bbl', 'bin', 'bin_evidence'])
               )
        $fn$
        """
    )


def _tables() -> None:
    op.execute(
        f"""
        CREATE TABLE records.retrieval_run (
            id                  uuid PRIMARY KEY,
            created_at          timestamptz NOT NULL DEFAULT now(),
            input_digest        text NOT NULL CHECK (input_digest ~ {HEX64}),
            resolution_outcome  text NOT NULL
                                CHECK (resolution_outcome IN
                                       ('verified', 'ambiguous', 'no_reliable_match',
                                        'source_unavailable')),
            resolution_level    text CHECK (resolution_level IN ('building', 'tax_lot')),
            resolved_bbl        text CHECK (resolved_bbl ~ {BBL}),
            resolved_bin        text CHECK (resolved_bin ~ {BIN}),
            resolution          jsonb NOT NULL,
            CONSTRAINT level_only_when_verified CHECK (
                (resolution_outcome = 'verified') = (resolution_level IS NOT NULL)
            ),
            CONSTRAINT bin_only_at_building_level CHECK (
                resolved_bin IS NULL OR resolution_level = 'building'
            ),
            CONSTRAINT bbl_when_verified CHECK (
                (resolution_outcome = 'verified') = (resolved_bbl IS NOT NULL)
            ),
            CONSTRAINT projection_whitelisted CHECK (records.resolution_projection_ok(resolution)),
            CONSTRAINT projection_matches CHECK (
                resolution ->> 'outcome' = resolution_outcome
                AND (resolution ->> 'resolution_level') IS NOT DISTINCT FROM resolution_level
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE records.source_fetch (
            id                  uuid PRIMARY KEY,
            run_id              uuid NOT NULL REFERENCES records.retrieval_run (id),
            idempotency_key     text NOT NULL UNIQUE CHECK (idempotency_key ~ {HEX64}),
            content_sha256      text NOT NULL CHECK (content_sha256 ~ {HEX64}),
            created_at          timestamptz NOT NULL DEFAULT now(),
            created_txid        xid8 NOT NULL,
            result_type         text NOT NULL CHECK (result_type IN ({RESULT_TYPES})),
            source              text NOT NULL CHECK (source <> ''),
            dataset_id          text NOT NULL CHECK (dataset_id ~ {DATASET}),
            status              text NOT NULL CHECK (status IN ({STATUSES})),
            status_detail       text NOT NULL,
            partial             boolean NOT NULL,
            join_scope          text CHECK (join_scope IN ('building', 'tax_lot')),
            resolved_bbl        text CHECK (resolved_bbl ~ {BBL}),
            resolved_bin        text CHECK (resolved_bin ~ {BIN}),
            source_count        integer CHECK (source_count >= 0),
            rows_fetched        integer NOT NULL CHECK (rows_fetched >= 0),
            record_count        integer NOT NULL CHECK (record_count >= 0),
            source_row_count    integer NOT NULL CHECK (source_row_count >= 0),
            page_count          integer NOT NULL CHECK (page_count >= 0),
            incomplete_reasons  text[] NOT NULL,
            warnings            text[] NOT NULL,
            data_as_of          text,
            where_clause        text,
            order_by            text,
            page_size           integer CHECK (page_size > 0),
            row_cap             integer CHECK (row_cap > 0),
            building_id_applied boolean NOT NULL,
            result_payload      jsonb NOT NULL CHECK (jsonb_typeof(result_payload) = 'object'),
            schema_version      integer NOT NULL CHECK (schema_version >= 1),
            UNIQUE (id, run_id),
            CONSTRAINT records_found_is_complete CHECK (
                status <> 'records_found' OR (
                    NOT partial AND cardinality(incomplete_reasons) = 0
                    AND source_count > 0 AND record_count > 0
                )
            ),
            CONSTRAINT no_records_found_is_complete CHECK (
                status <> 'no_records_found' OR (
                    NOT partial AND cardinality(incomplete_reasons) = 0
                    AND source_count = 0 AND rows_fetched = 0 AND record_count = 0
                )
            ),
            CONSTRAINT incomplete_is_partial CHECK (
                status <> 'incomplete_pagination'
                OR (partial AND cardinality(incomplete_reasons) > 0)
            ),
            CONSTRAINT failures_have_no_records CHECK (
                status NOT IN ('source_unavailable', 'unsupported_join')
                OR (NOT partial AND record_count = 0 AND source_row_count = 0)
            ),
            CONSTRAINT unsupported_join_made_no_requests CHECK (
                status <> 'unsupported_join' OR (page_count = 0 AND join_scope IS NULL)
            ),
            CONSTRAINT bin_only_at_building_scope CHECK (
                resolved_bin IS NULL OR join_scope = 'building'
            ),
            CONSTRAINT links_within_rows_fetched CHECK (source_row_count <= rows_fetched),
            CONSTRAINT payload_matches CHECK (
                result_payload ->> 'source' = source
                AND result_payload ->> 'dataset_id' = dataset_id
                AND result_payload ->> 'status' = status
                AND result_payload ->> 'status_detail' = status_detail
                AND (result_payload ->> 'partial')::boolean = partial
                AND (result_payload ->> 'join_scope') IS NOT DISTINCT FROM join_scope
                AND (result_payload ->> 'resolved_bbl') IS NOT DISTINCT FROM resolved_bbl
                AND (result_payload ->> 'resolved_bin') IS NOT DISTINCT FROM resolved_bin
                AND (result_payload #>> '{{provenance,source_count}}')::integer
                    IS NOT DISTINCT FROM source_count
                AND (result_payload #>> '{{provenance,rows_fetched}}')::integer = rows_fetched
                AND NOT (result_payload ? 'records')
                AND NOT (result_payload -> 'provenance' ? 'pages')
            ),
            CONSTRAINT payload_has_no_private_keys CHECK (
                records.forbidden_keys_absent(result_payload)
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE records.fetch_page (
            fetch_id             uuid NOT NULL REFERENCES records.source_fetch (id),
            seq                  integer NOT NULL CHECK (seq >= 0),
            batch                text,
            page_index           integer NOT NULL CHECK (page_index >= -1),
            url                  text NOT NULL CHECK (url ~ {SOCRATA_URL}),
            rows                 integer NOT NULL CHECK (rows >= 0),
            fetched_at           text NOT NULL,
            attempts             integer NOT NULL CHECK (attempts >= 1),
            truth_last_modified  text,
            last_modified        text,
            request_id           text,
            PRIMARY KEY (fetch_id, seq)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE records.normalized_record (
            id                    uuid PRIMARY KEY,
            fetch_id              uuid NOT NULL REFERENCES records.source_fetch (id),
            ordinal               integer NOT NULL CHECK (ordinal >= 0),
            record_kind           text NOT NULL CHECK (record_kind IN ({RECORD_KINDS})),
            evidence_kind         text NOT NULL CHECK (evidence_kind IN
                                  ('official_finding', 'reported_allegation',
                                   'administrative_record')),
            dataset_id            text NOT NULL CHECK (dataset_id ~ {DATASET}),
            source_record_id      text NOT NULL CHECK (source_record_id <> ''),
            resolved_bbl          text NOT NULL CHECK (resolved_bbl ~ {BBL}),
            lot_membership        text CHECK (lot_membership IN ('confirmed', 'conflicting')),
            attribution           text NOT NULL CHECK (attribution IN ({ATTRIBUTIONS})),
            attribution_basis     text NOT NULL
                                  CHECK (attribution_basis IN
                                         ('row_identifiers', 'hpd_building_id')),
            original_attribution  text CHECK (original_attribution IN ({ATTRIBUTIONS})),
            mapped_bin            text CHECK (mapped_bin ~ {BIN}),
            location_scope        text CHECK (location_scope IN
                                  ('unit', 'common_area', 'building_wide', 'unspecified')),
            quality_flags         text[] NOT NULL,
            digest_basis          text NOT NULL
                                  CHECK (digest_basis IN ('original_row', 'sanitized_row')),
            original_digest       text NOT NULL CHECK (original_digest ~ {HEX64}),
            redacted_sha256       text NOT NULL CHECK (redacted_sha256 ~ {HEX64}),
            payload               jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            schema_version        integer NOT NULL CHECK (schema_version >= 1),
            UNIQUE (id, fetch_id),
            UNIQUE (fetch_id, ordinal),
            CONSTRAINT building_id_basis CHECK (
                attribution_basis <> 'hpd_building_id' OR (
                    original_attribution = 'unattributed_on_lot'
                    AND mapped_bin IS NOT NULL
                    AND lot_membership = 'confirmed'
                    AND attribution IN ('this_building', 'other_building_on_lot')
                )
            ),
            CONSTRAINT row_identifier_basis CHECK (
                attribution_basis <> 'row_identifiers'
                OR (original_attribution IS NULL AND mapped_bin IS NULL)
            ),
            CONSTRAINT payload_matches CHECK (
                payload ->> 'dataset_id' = dataset_id
                AND payload ->> 'evidence_kind' = evidence_kind
                AND payload ->> 'resolved_bbl' = resolved_bbl
                AND (payload ->> 'lot_membership') IS NOT DISTINCT FROM lot_membership
                AND payload ->> 'attribution' = attribution
                AND payload ->> 'attribution_basis' = attribution_basis
                AND (payload ->> 'original_attribution') IS NOT DISTINCT FROM original_attribution
                AND (payload ->> 'mapped_bin') IS NOT DISTINCT FROM mapped_bin
                AND (payload ->> 'location_scope') IS NOT DISTINCT FROM location_scope
                AND payload ->> 'digest_basis' = digest_basis
                AND payload ->> 'original_digest' = original_digest
                AND payload ->> 'redacted_sha256' = redacted_sha256
                AND (record_kind <> 'violation' OR payload ->> 'violation_id' = source_record_id)
            ),
            CONSTRAINT payload_has_no_private_keys CHECK (records.forbidden_keys_absent(payload))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE records.record_source_row (
            record_id         uuid NOT NULL,
            fetch_id          uuid NOT NULL,
            position          integer NOT NULL CHECK (position >= 0),
            source_record_id  text NOT NULL CHECK (source_record_id <> ''),
            source_row_id     text,
            original_digest   text NOT NULL REFERENCES evidence.raw_row (original_digest),
            PRIMARY KEY (record_id, position),
            UNIQUE (fetch_id, original_digest),
            FOREIGN KEY (record_id, fetch_id) REFERENCES records.normalized_record (id, fetch_id)
        )
        """
    )
    op.execute("CREATE INDEX source_fetch_run ON records.source_fetch (run_id)")
    op.execute(
        "CREATE INDEX record_source_row_digest ON records.record_source_row (original_digest)"
    )


def _triggers() -> None:
    # Every trigger function runs with the inserting role's own rights (no SECURITY
    # DEFINER); ingest_writer can read records.* and the availability view.
    op.execute(
        """
        CREATE FUNCTION records.source_fetch_before_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE r records.retrieval_run;
        BEGIN
            NEW.created_txid := pg_current_xact_id();
            SELECT * INTO r FROM records.retrieval_run WHERE id = NEW.run_id;
            IF NEW.join_scope = 'building' AND r.resolution_level IS DISTINCT FROM 'building' THEN
                RAISE EXCEPTION 'a building-scope fetch needs a building-level resolution'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.resolved_bbl IS NOT NULL AND NEW.resolved_bbl IS DISTINCT FROM r.resolved_bbl
               OR NEW.resolved_bin IS NOT NULL AND NEW.resolved_bin IS DISTINCT FROM r.resolved_bin
            THEN
                RAISE EXCEPTION 'fetch identifiers differ from the run''s resolution'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER before_insert BEFORE INSERT ON records.source_fetch "
        "FOR EACH ROW EXECUTE FUNCTION records.source_fetch_before_insert()"
    )

    # Shared by pages, records and links: the fetch must have been inserted by this
    # same transaction, so a committed snapshot can never be added to.
    op.execute(
        """
        CREATE FUNCTION records.open_fetch(p_fetch uuid) RETURNS records.source_fetch
        LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch;
        BEGIN
            SELECT * INTO f FROM records.source_fetch WHERE id = p_fetch;
            IF f.id IS NULL THEN
                RETURN f;  -- the foreign key reports the missing fetch
            END IF;
            IF f.created_txid <> pg_current_xact_id() THEN
                RAISE EXCEPTION 'snapshot is sealed: rows cannot be added to a committed fetch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN f;
        END
        $fn$
        """
    )
    op.execute(
        """
        CREATE FUNCTION records.fetch_page_before_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            PERFORM records.open_fetch(NEW.fetch_id);
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER before_insert BEFORE INSERT ON records.fetch_page "
        "FOR EACH ROW EXECUTE FUNCTION records.fetch_page_before_insert()"
    )
    op.execute(
        """
        CREATE FUNCTION records.normalized_record_before_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE f records.source_fetch;
        BEGIN
            f := records.open_fetch(NEW.fetch_id);
            IF f.id IS NULL THEN
                RETURN NEW;
            END IF;
            IF f.status NOT IN ('records_found', 'incomplete_pagination') THEN
                RAISE EXCEPTION 'records are not allowed under a % fetch', f.status
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.attribution IN ('this_building', 'other_building_on_lot')
               AND f.join_scope IS DISTINCT FROM 'building' THEN
                RAISE EXCEPTION 'building-level attribution requires a building-scope fetch'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.attribution_basis = 'hpd_building_id'
               AND f.join_scope IS DISTINCT FROM 'building' THEN
                RAISE EXCEPTION 'BuildingID attribution requires a building-scope fetch'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.resolved_bbl IS DISTINCT FROM f.resolved_bbl
               OR NEW.dataset_id <> f.dataset_id THEN
                RAISE EXCEPTION 'record lot or dataset differs from its fetch'
                    USING ERRCODE = '23514';
            END IF;
            IF f.result_type = 'hpd_violations' AND NEW.record_kind <> 'violation' THEN
                RAISE EXCEPTION 'record kind does not match the fetch result type'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER before_insert BEFORE INSERT ON records.normalized_record "
        "FOR EACH ROW EXECUTE FUNCTION records.normalized_record_before_insert()"
    )
    op.execute(
        """
        CREATE FUNCTION records.record_source_row_before_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            PERFORM records.open_fetch(NEW.fetch_id);
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER before_insert BEFORE INSERT ON records.record_source_row "
        "FOR EACH ROW EXECUTE FUNCTION records.record_source_row_before_insert()"
    )

    op.execute(
        """
        CREATE FUNCTION records.check_fetch_complete() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
            f records.source_fetch;
            n_records integer;
            n_pages integer;
            n_links integer;
            collapsed boolean;
        BEGIN
            SELECT * INTO f FROM records.source_fetch WHERE id = NEW.id;
            SELECT count(*) INTO n_records FROM records.normalized_record WHERE fetch_id = f.id;
            SELECT count(*) INTO n_pages FROM records.fetch_page WHERE fetch_id = f.id;
            SELECT count(*) INTO n_links FROM records.record_source_row WHERE fetch_id = f.id;
            IF n_records <> f.record_count OR n_pages <> f.page_count
               OR n_links <> f.source_row_count THEN
                RAISE EXCEPTION 'fetch %: stored counts do not match its rows', f.id
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM records.normalized_record r
                WHERE r.fetch_id = f.id AND NOT EXISTS (
                    SELECT 1 FROM records.record_source_row l
                    WHERE l.record_id = r.id AND l.position = 0
                      AND l.original_digest = r.original_digest)
            ) THEN
                RAISE EXCEPTION 'fetch %: a record''s first source row is not its own row', f.id
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM records.record_source_row
                WHERE fetch_id = f.id GROUP BY record_id HAVING max(position) + 1 <> count(*)
            ) THEN
                RAISE EXCEPTION 'fetch %: source-row positions have gaps', f.id
                    USING ERRCODE = '23514';
            END IF;
            IF f.status = 'records_found' THEN
                SELECT EXISTS (
                    SELECT 1 FROM records.normalized_record
                    WHERE fetch_id = f.id AND 'duplicate_id_same_content' = ANY (quality_flags)
                ) INTO collapsed;
                IF (collapsed AND n_links >= f.source_count)
                   OR (NOT collapsed AND n_links <> f.source_count) THEN
                    RAISE EXCEPTION 'fetch %: source rows do not account for the source count', f.id
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF EXISTS (
                SELECT 1 FROM records.record_source_row l
                JOIN records.normalized_record r ON r.id = l.record_id
                JOIN records.evidence_availability e ON e.original_digest = l.original_digest
                WHERE l.fetch_id = f.id AND e.basis <> r.digest_basis
            ) THEN
                RAISE EXCEPTION
                    'fetch %: raw row basis differs from the record''s digest basis', f.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $fn$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER complete_at_commit AFTER INSERT ON records.source_fetch "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION records.check_fetch_complete()"
    )


def downgrade() -> None:
    with as_owner():
        for table in ("record_source_row", "normalized_record", "fetch_page"):
            op.execute(f"DROP TABLE records.{table}")
        for fn in (
            "record_source_row_before_insert()",
            "normalized_record_before_insert()",
            "fetch_page_before_insert()",
            "open_fetch(uuid)",  # returns the source_fetch row type
        ):
            op.execute(f"DROP FUNCTION records.{fn}")
        op.execute("DROP TABLE records.source_fetch")
        op.execute("DROP TABLE records.retrieval_run")
        for fn in (
            "check_fetch_complete()",
            "source_fetch_before_insert()",
            "resolution_projection_ok(jsonb)",
            "keys_within(jsonb, text[])",
            "forbidden_keys_absent(jsonb)",
        ):
            op.execute(f"DROP FUNCTION records.{fn}")
