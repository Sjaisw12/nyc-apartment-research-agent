"""Dataset freshness from the Socrata catalog entry (`/api/views/<id>.json`).

Kept separate from row retrieval: a failure here never discards verified rows. The
result says freshness could not be judged instead.
"""

from __future__ import annotations

import datetime as dt
import logging

from ..errors import SourceError
from ..http_client import SourceHttpClient
from ..records.models import DataFreshness

logger = logging.getLogger(__name__)

# Days after which a dataset is called stale, by its stated update frequency (cadence
# plus a margin). Unknown frequencies are not judged.
STALE_AFTER_DAYS = {"daily": 3, "weekly": 10, "monthly": 45, "quarterly": 120, "annually": 400}


def _iso(ts: object) -> str | None:
    if isinstance(ts, int | float) and ts > 0:
        return dt.datetime.fromtimestamp(ts, dt.UTC).isoformat()
    return None


def _frequency(meta: dict) -> str | None:
    custom = (meta.get("metadata") or {}).get("custom_fields") or {}
    for group in custom.values():
        if isinstance(group, dict) and group.get("Update Frequency"):
            return str(group["Update Frequency"])
    return None


def fetch_freshness(
    client: SourceHttpClient, dataset_id: str, *, today: dt.date | None = None
) -> DataFreshness:
    today = today or dt.datetime.now(dt.UTC).date()
    try:
        resp = client.get_json(f"/api/views/{dataset_id}.json")
    except SourceError as exc:
        logger.warning("freshness for %s unavailable: %s", dataset_id, type(exc).__name__)
        return DataFreshness(
            dataset_id=dataset_id, checked_on=today.isoformat(), error=type(exc).__name__
        )
    meta = resp.data if isinstance(resp.data, dict) else {}
    data_updated = _iso(meta.get("rowsUpdatedAt"))
    freq = _frequency(meta)
    threshold = STALE_AFTER_DAYS.get((freq or "").strip().lower())
    days = None
    if data_updated:
        days = (today - dt.datetime.fromisoformat(data_updated).date()).days
    return DataFreshness(
        dataset_id=dataset_id,
        data_updated_at=data_updated,
        metadata_updated_at=_iso(meta.get("viewLastModified")),
        stated_update_frequency=freq,
        checked_on=today.isoformat(),
        days_since_data_update=days,
        stale_threshold_days=threshold,
        stale=(days > threshold) if days is not None and threshold is not None else None,
    )
