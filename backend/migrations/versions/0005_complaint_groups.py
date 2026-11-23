"""HPD complaints: problem records and complaint groups (Task 4, step 4).

Additive on top of 0004; nothing from 0004 is replaced except one CHECK on
source_fetch.result_payload (see below).

A complaint (complaint_id) holds one or more problem rows (problem_id). Problems are
stored as normalized_record rows (record_kind = complaint_problem) with their
complaint_id in a typed column; each complaint is one records.complaint_group row.

Database-enforced:
  - complaint_group CHECKs: attribution is 'mixed' exactly when its problems span more
    than one bucket, and otherwise names the single bucket they share; counts are
    consistent; typed columns equal the payload; no private keys in the payload.
  - normalized_record: a complaint_problem has a complaint_id (and only it does), equal
    to its payload; problem_id = source_record_id; FK (fetch_id, complaint_id) to its
    complaint_group, so no problem exists without its complaint.
  - Row triggers: groups only under a complaints fetch with records; building-level
    group attribution only at building scope; complaint problems only under a complaints
    fetch and vice versa; groups cannot be added to a committed snapshot.
  - At commit: the group count matches; every group's problem count, duplicate count,
    problem_ids and per-bucket counts equal its actual problem records, and its
    attribution follows from them. A group can never claim a building its problems
    do not support.
  - source_fetch.result_payload: the complaint list lives only in complaint_group. The
    summary's withheld_field_counts is keyed by the NAMES of withheld fields
    (complaint_anonymous_flag, unique_key) with numeric counts; the private-key check
    now ignores that one object, and requires its values to be numbers.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

ATTRIBUTIONS = "'this_building', 'other_building_on_lot', 'unattributed_on_lot', 'lot_level'"
WITHHELD_COUNTS_ARE_NUMBERS = (
    "NOT jsonb_path_exists(result_payload, "
    "'lax $.summary.withheld_field_counts.* ? (@.type() != \"number\")')"
)


def upgrade() -> None:
    with as_owner():
        op.execute(
            """
            CREATE FUNCTION records.jsonb_key_count(j jsonb) RETURNS integer
            LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
            AS $fn$
                SELECT CASE WHEN jsonb_typeof(j) = 'object'
                            THEN (SELECT count(*)::integer FROM jsonb_object_keys(j))
                            ELSE 0 END
            $fn$
            """
        )
        op.execute(
            """
            CREATE FUNCTION records.jsonb_int_sum(j jsonb) RETURNS bigint
            LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
            AS $fn$
                SELECT coalesce(sum(value::bigint), 0) FROM jsonb_each_text(j)
            $fn$
            """
        )
        op.execute(
            f"""
            CREATE TABLE records.complaint_group (
                id                       uuid PRIMARY KEY,
                fetch_id                 uuid NOT NULL REFERENCES records.source_fetch (id),
                ordinal                  integer NOT NULL CHECK (ordinal >= 0),
                complaint_id             text NOT NULL CHECK (complaint_id <> ''),
                attribution              text NOT NULL
                                         CHECK (attribution IN ({ATTRIBUTIONS}, 'mixed')),
                problem_attributions     jsonb NOT NULL,
                problem_count            integer NOT NULL CHECK (problem_count >= 1),
                duplicate_problem_count  integer NOT NULL CHECK (duplicate_problem_count >= 0),
                payload                  jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
                schema_version           integer NOT NULL CHECK (schema_version >= 1),
                UNIQUE (fetch_id, complaint_id),
                UNIQUE (fetch_id, ordinal),
                CONSTRAINT mixed_means_several_buckets CHECK (
                    (attribution = 'mixed') = (records.jsonb_key_count(problem_attributions) > 1)
                ),
                CONSTRAINT single_bucket_is_the_attribution CHECK (
                    attribution = 'mixed' OR problem_attributions ? attribution
                ),
                CONSTRAINT buckets_are_known CHECK (
                    records.keys_within(problem_attributions,
                        ARRAY['this_building', 'other_building_on_lot',
                              'unattributed_on_lot', 'lot_level'])
                ),
                CONSTRAINT bucket_counts_add_up CHECK (
                    records.jsonb_int_sum(problem_attributions) = problem_count
                ),
                CONSTRAINT duplicates_within_problems CHECK (
                    duplicate_problem_count <= problem_count
                ),
                CONSTRAINT payload_matches CHECK (
                    payload ->> 'complaint_id' = complaint_id
                    AND payload ->> 'attribution' = attribution
                    AND payload -> 'problem_attributions' = problem_attributions
                    AND (payload ->> 'problem_count')::integer = problem_count
                    AND (payload ->> 'duplicate_problem_count')::integer = duplicate_problem_count
                    AND jsonb_array_length(payload -> 'problem_ids') = problem_count
                    AND payload ->> 'evidence_kind' = 'reported_allegation'
                ),
                CONSTRAINT payload_has_no_private_keys CHECK (
                    records.forbidden_keys_absent(payload)
                )
            )
            """
        )

        op.execute("ALTER TABLE records.normalized_record ADD COLUMN complaint_id text")
        op.execute(
            """
            ALTER TABLE records.normalized_record ADD CONSTRAINT complaint_problem_identity CHECK (
                (record_kind = 'complaint_problem') = (complaint_id IS NOT NULL)
                AND (record_kind <> 'complaint_problem' OR (
                    payload ->> 'complaint_id' = complaint_id
                    AND payload ->> 'problem_id' = source_record_id
                    AND evidence_kind = 'reported_allegation'
                ))
            )
            """
        )
        op.execute(
            "ALTER TABLE records.normalized_record ADD CONSTRAINT problem_has_its_complaint "
            "FOREIGN KEY (fetch_id, complaint_id) "
            "REFERENCES records.complaint_group (fetch_id, complaint_id)"
        )
        op.execute(
            "CREATE INDEX normalized_record_complaint ON records.normalized_record "
            "(fetch_id, complaint_id) WHERE complaint_id IS NOT NULL"
        )

        op.execute(
            "ALTER TABLE records.source_fetch "
            "ADD COLUMN group_count integer NOT NULL DEFAULT 0 CHECK (group_count >= 0)"
        )
        op.execute(
            """
            ALTER TABLE records.source_fetch ADD CONSTRAINT groups_only_for_complaints CHECK (
                (result_type = 'hpd_complaints' OR group_count = 0)
                AND group_count <= record_count
                AND (result_type <> 'hpd_complaints' OR status <> 'records_found'
                     OR group_count > 0)
                AND NOT (result_payload ? 'complaints')
            )
            """
        )
        op.execute("ALTER TABLE records.source_fetch DROP CONSTRAINT payload_has_no_private_keys")
        op.execute(
            "ALTER TABLE records.source_fetch ADD CONSTRAINT payload_has_no_private_keys CHECK ("
            "records.forbidden_keys_absent(result_payload #- '{summary,withheld_field_counts}') "
            f"AND {WITHHELD_COUNTS_ARE_NUMBERS})"
        )

        op.execute(
            """
            CREATE FUNCTION records.complaint_group_before_insert() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
            AS $fn$
            DECLARE f records.source_fetch;
            BEGIN
                f := records.open_fetch(NEW.fetch_id);
                IF f.id IS NULL THEN
                    RETURN NEW;
                END IF;
                IF f.result_type <> 'hpd_complaints' THEN
                    RAISE EXCEPTION 'complaint groups belong only to a complaints fetch'
                        USING ERRCODE = '23514';
                END IF;
                IF f.status NOT IN ('records_found', 'incomplete_pagination') THEN
                    RAISE EXCEPTION 'complaint groups are not allowed under a % fetch', f.status
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
            "CREATE TRIGGER before_insert BEFORE INSERT ON records.complaint_group "
            "FOR EACH ROW EXECUTE FUNCTION records.complaint_group_before_insert()"
        )
        op.execute(
            """
            CREATE FUNCTION records.complaint_record_kind() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
            AS $fn$
            DECLARE rt text;
            BEGIN
                SELECT result_type INTO rt FROM records.source_fetch WHERE id = NEW.fetch_id;
                IF (rt = 'hpd_complaints') <> (NEW.record_kind = 'complaint_problem') THEN
                    RAISE EXCEPTION 'record kind does not match the fetch result type'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END
            $fn$
            """
        )
        op.execute(
            "CREATE TRIGGER complaint_kind BEFORE INSERT ON records.normalized_record "
            "FOR EACH ROW EXECUTE FUNCTION records.complaint_record_kind()"
        )

        op.execute(
            """
            CREATE FUNCTION records.check_complaint_groups() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
            AS $fn$
            DECLARE f records.source_fetch;
            BEGIN
                SELECT * INTO f FROM records.source_fetch WHERE id = NEW.id;
                IF (SELECT count(*) FROM records.complaint_group WHERE fetch_id = f.id)
                   <> f.group_count THEN
                    RAISE EXCEPTION 'fetch %: stored group count does not match its groups', f.id
                        USING ERRCODE = '23514';
                END IF;
                IF EXISTS (
                    WITH problems AS (
                        SELECT complaint_id, attribution, source_record_id,
                               (payload ->> 'is_duplicate_report')::boolean AS dup
                        FROM records.normalized_record WHERE fetch_id = f.id
                    ),
                    actual AS (
                        SELECT complaint_id, count(*) AS n,
                               count(*) FILTER (WHERE dup) AS dups,
                               array_agg(source_record_id ORDER BY source_record_id) AS ids
                        FROM problems GROUP BY complaint_id
                    ),
                    buckets AS (
                        SELECT complaint_id, jsonb_object_agg(attribution, n) AS b,
                               count(*) AS nb, min(attribution) AS only_bucket
                        FROM (SELECT complaint_id, attribution, count(*) AS n
                              FROM problems GROUP BY 1, 2) x
                        GROUP BY complaint_id
                    )
                    SELECT 1
                    FROM records.complaint_group g
                    LEFT JOIN actual a ON a.complaint_id = g.complaint_id
                    LEFT JOIN buckets b ON b.complaint_id = g.complaint_id
                    WHERE g.fetch_id = f.id AND (
                        a.n IS NULL
                        OR a.n <> g.problem_count
                        OR a.dups <> g.duplicate_problem_count
                        OR b.b <> g.problem_attributions
                        OR g.attribution <>
                           CASE WHEN b.nb = 1 THEN b.only_bucket ELSE 'mixed' END
                        OR a.ids <> ARRAY(
                            SELECT x FROM jsonb_array_elements_text(g.payload -> 'problem_ids') x
                            ORDER BY x)
                    )
                ) THEN
                    RAISE EXCEPTION
                        'fetch %: a complaint group does not match its problem records', f.id
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END
            $fn$
            """
        )
        op.execute(
            "CREATE CONSTRAINT TRIGGER complaint_groups_at_commit AFTER INSERT "
            "ON records.source_fetch DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION records.check_complaint_groups()"
        )
        op.execute(
            "GRANT EXECUTE ON FUNCTION records.jsonb_key_count(jsonb), "
            "records.jsonb_int_sum(jsonb) TO ingest_writer"
        )


def downgrade() -> None:
    with as_owner():
        op.execute("DROP TRIGGER complaint_groups_at_commit ON records.source_fetch")
        op.execute("DROP FUNCTION records.check_complaint_groups()")
        op.execute("DROP TRIGGER complaint_kind ON records.normalized_record")
        op.execute("DROP FUNCTION records.complaint_record_kind()")
        op.execute("ALTER TABLE records.source_fetch DROP CONSTRAINT payload_has_no_private_keys")
        op.execute(
            "ALTER TABLE records.source_fetch ADD CONSTRAINT payload_has_no_private_keys "
            "CHECK (records.forbidden_keys_absent(result_payload))"
        )
        op.execute("ALTER TABLE records.source_fetch DROP CONSTRAINT groups_only_for_complaints")
        op.execute("ALTER TABLE records.source_fetch DROP COLUMN group_count")
        op.execute("ALTER TABLE records.normalized_record DROP COLUMN complaint_id")
        op.execute("DROP TABLE records.complaint_group")
        op.execute("DROP FUNCTION records.complaint_group_before_insert()")
        op.execute("DROP FUNCTION records.jsonb_int_sum(jsonb)")
        op.execute("DROP FUNCTION records.jsonb_key_count(jsonb)")
