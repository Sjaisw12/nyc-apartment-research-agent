import hashlib
import json
from pathlib import Path

import pytest

from nyc_apartment_agent.http_client import SourceResponse

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(source: str, name: str) -> SourceResponse:
    """Load a saved live response and check it still matches its recorded SHA-256."""
    body_path = FIXTURES / source / f"{name}.json"
    meta = json.loads((FIXTURES / source / f"{name}.meta.json").read_text(encoding="utf-8"))
    body = body_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert digest == meta["body_sha256"], f"fixture {body_path} was modified after capture"
    return SourceResponse(
        source=meta["source"],
        url=meta["url"],
        status_code=meta["status_code"],
        data=json.loads(body),
        headers=meta["headers"],
        attempts=meta["attempts"],
        elapsed_seconds=meta["elapsed_seconds"],
        fetched_at=meta["fetched_at"],
    )


@pytest.fixture
def fixture_loader():
    return load_fixture
