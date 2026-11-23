# Raw-evidence retention: operations runbook

Status: **not deployed.** Nothing in this repository schedules, runs or monitors the
retention job anywhere. This runbook says how a future deployment should run it. No
deployment has been authorized.

## 1. What the job does

`backend/scripts/run_retention.py` runs `storage.retention.run_retention` once. For every
raw row in `evidence.raw_row` whose `retain_until` has passed by the database clock, it:

- sets `original_row` and `original_sha256` to NULL and `expired_at` to the run's cutoff;
- leaves the keyed digest, dataset and source ids, basis, redacted hash, query URL, fetch
  time, retention date and restore history unchanged;
- touches nothing in the `records` schema.

Public records keep their lineage and report the evidence as not held. Retention is at
most 180 days from the fetch whose copy is held (D15), plus the gap until the next run.

The database enforces the expiry rules itself (migration 0010): a row the database clock
does not consider due cannot be expired by anyone, held rows and tombstones cannot be
otherwise changed, and only `evidence.restore_expired_raw_row` can restore a tombstone,
with a copy fetched after the expiry.

## 2. Prerequisites

1. Migrations at head (`0010` or later).
2. A dedicated login that holds **only** `evidence_retention`. Example, run by an
   administrator; the password comes from the secret store, never from git:

   ```sql
   CREATE ROLE nyc_retention LOGIN PASSWORD :'password' IN ROLE evidence_retention;
   ALTER ROLE nyc_retention IN DATABASE nyc SET log_error_verbosity = terse;
   ```

   The job refuses a login that can read `original_row` (an `evidence_admin` member, the
   owner, a superuser) or that lacks `evidence_retention`.
3. `RETENTION_DATABASE_URL` set to that login's URL in the scheduler's secret store. The
   job does not need `EVIDENCE_DIGEST_KEY` and never reads row content.

## 3. Schedule

- Run **once a day** (for example 03:15 UTC) from the hosting platform's cron facility
  (a Render or Railway cron job, or an equivalent), in the same network as the database.
  Daily runs bound the hold at 180 days plus about one day.
- Allow at most one scheduled instance at a time. Overlapping runs are nevertheless safe:
  rows are taken with `FOR UPDATE SKIP LOCKED`, so no row is expired twice and no run
  waits on another.
- Defaults: batches of 500 rows, at most 100 batches per run (50,000 rows), 5 s lock
  timeout, 60 s statement timeout per batch. Each batch commits on its own.

```
RETENTION_DATABASE_URL=... uv run python scripts/run_retention.py
uv run python scripts/run_retention.py --dry-run     # count due rows; change nothing
```

## 4. Output, exit codes and monitoring

The script prints one JSON line with counts only, for example:

```
{"status": "complete", "cutoff": "...", "expired": 120, "batches": 1, "remaining_due": 0, "error": null}
```

| Exit | Status | Meaning | Action |
|---|---|---|---|
| 0 | `complete` | every row due at the cutoff is a tombstone | none |
| 3 | `incomplete` | stopped at `--max-batches`, or skipped rows locked by another run | it resumes on the next run; alert if two runs in a row end here |
| 1 | `failed` | refused, or a database error | alert at once; batches committed before the error are kept; safe to retry |
| 2 | `not_configured` | `RETENTION_DATABASE_URL` missing | fix the scheduler's secrets |

Alerts a deployment should have:

1. Any exit 1, or two consecutive exit 3.
2. No exit 0 in 36 hours (a dead-man's-switch heartbeat; a scheduler that silently stops
   is the most likely failure).
3. Overdue evidence, checked independently of the job, with the retention login:

   ```sql
   SELECT count(*), min(retain_until)
     FROM evidence.raw_row
    WHERE expired_at IS NULL AND retain_until <= now() - interval '2 days';
   ```

   Any row here means evidence is held past its window. This query reads no content.

The JSON line and the log record carry no row content, no digests and no credentials.

## 5. Failure handling

The job is idempotent: rerunning it with the same cutoff expires nothing more, and a
rerun after a failure continues where the committed batches stopped. Never fix a failure
by widening the job's privileges.

| Error in the report | Likely cause | Fix |
|---|---|---|
| `login lacks evidence_retention` | wrong login or role not granted | grant the role to the dedicated login |
| `login can read raw evidence content; refused` | the URL names an admin, owner or superuser | use the dedicated login |
| `InsufficientPrivilege (42501)` | login without `CONNECT` or schema usage | check the grants from migration 0001 |
| `LockNotAvailable (55P03)` | a long transaction holds raw rows | rerun; find the holder if it repeats |
| `QueryCanceled (57014)` | statement timeout | rerun with a smaller `--batch-size` |
| `CheckViolation (23514)` | a lifecycle rule refused an update | investigate; do not bypass the trigger |
| `OperationalError (...)` | database unreachable | rerun when it is back |

## 6. Backups, point-in-time recovery and copies

Expiry removes originals from the **primary database only**.

- Backups, WAL archives used for point-in-time recovery (PITR), snapshots and any
  replica or `pg_dump` taken before an expiry still contain the original rows. The
  practical deletion bound is therefore **180 days + the backup and PITR retention
  period + one scheduling interval**. That period is set by the hosting provider and is
  not known until deployment.
- Requirements for any deployment:
  - backups and WAL archives encrypted at rest, with keys managed by the provider or a
    KMS, never in this repository;
  - restore and download rights limited to named operators, and every restore recorded;
  - bounded backup and PITR retention (for example 7 to 30 days), stated in the privacy
    notice;
  - no ad hoc dumps of the `evidence` schema; a dump made by `evidence_admin` or the
    owner is itself a backup and must follow these rules;
  - **after any restore or PITR, run the retention job immediately, before the
    application serves traffic.** `retain_until` values are absolute timestamps, so the
    first run after a restore expires everything that became due in the meantime. Then
    run the overdue check in section 4.
- A restored database does not restart any window: the retention dates come back with
  the rows.

## 7. Server logs

Application-side scrubbing (`hide_parameters=True`, scrubbed write errors, no DEBUG row
logging) covers what the application logs. It does **not** sanitize what PostgreSQL
writes to its own server log. Measured on the local PostgreSQL 16 test cluster on
2026-10-23 (a failed raw-row insert carrying a sentinel apartment value, run through the
application's own engine with bound parameters):

| Server setting for the login | Sentinel lines in the server log |
|---|---|
| `log_error_verbosity = default` | 1 (the `DETAIL: Failing row contains (...)` line quotes the row) |
| `log_error_verbosity = terse` | 0 (the `STATEMENT` line shows `$1` placeholders only) |
| `log_statement = all`, `log_parameter_max_length = -1` (default) | 1 (bound values logged) |
| `log_statement = all`, `log_parameter_max_length = 0` | 0 |

Required server settings for the application database (superuser or provider parameter
group; set per database or per login):

- `log_error_verbosity = terse`;
- `log_statement = 'none'` or `'ddl'`, and `log_parameter_max_length = 0`;
- `log_parameter_max_length_on_error = 0` (the default);
- `log_min_duration_statement` and `auto_explain` off, or with parameter logging off.

These are deployment settings. The tests prove only that a per-login `terse` setting
takes effect and that bound values never appear in statement text; they do not prove
what any deployed server logs. Server logs must also have bounded retention and
restricted access.
