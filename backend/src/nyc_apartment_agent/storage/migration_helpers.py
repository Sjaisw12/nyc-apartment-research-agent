"""Run a migration's DDL as `nyc_owner`, so every object it creates is owned by that role.

Alembic runs each migration in its own transaction (transaction_per_migration=True).
`SET LOCAL ROLE` lasts until that transaction ends; `RESET ROLE` before returning lets
Alembic update `alembic_version` (owned by the migrating login) as the login itself.
If the migration fails, the transaction is rolled back and the role setting with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from alembic import op

OWNER = "nyc_owner"


@contextmanager
def as_owner() -> Iterator[None]:
    op.execute(f"SET LOCAL ROLE {OWNER}")
    yield
    op.execute("RESET ROLE")
