"""The k311_ token for 311 unique keys (D20, design 18.1). SYNTHETIC keys only."""

import hashlib
import hmac

import pytest

from nyc_apartment_agent.records.redaction import (
    K311_DOMAIN,
    K311_PATTERN,
    k311_token,
    keyed_digest,
)

KEY = b"synthetic-token-key"


def test_token_matches_the_contract_exactly():
    mac = hmac.new(KEY, b"nyc311-unique-key\x00" + b"61234567", hashlib.sha256).hexdigest()
    assert k311_token("61234567", KEY) == "k311_" + mac[:32]
    assert K311_DOMAIN == b"nyc311-unique-key\x00"


def test_format_is_prefix_plus_32_lowercase_hex():
    t = k311_token("61234567", KEY)
    assert K311_PATTERN.match(t) and len(t) == 37 and t == t.lower()


def test_stable_for_the_same_key_and_secret():
    assert k311_token("61234567", KEY) == k311_token("61234567", KEY)


def test_distinct_keys_give_distinct_tokens():
    tokens = {k311_token(str(60_000_000 + i), KEY) for i in range(2000)}
    assert len(tokens) == 2000


def test_key_rotation_changes_every_token():
    assert k311_token("61234567", KEY) != k311_token("61234567", b"rotated-key")


def test_domain_separated_from_row_digests():
    # The same secret over the bare key (or a row holding it) gives an unrelated value.
    bare = hmac.new(KEY, b"61234567", hashlib.sha256).hexdigest()
    assert k311_token("61234567", KEY)[5:] != bare[:32]
    digest = keyed_digest({"unique_key": "61234567"}, KEY)
    assert k311_token("61234567", KEY)[5:] not in digest


def test_the_key_value_never_appears_in_the_token():
    assert "61234567" not in k311_token("61234567", KEY)


@pytest.mark.parametrize(
    "bad", [None, "", " ", "[WITHHELD]", "6123 4567", "-5", "1.5", "0x1F", "1" * 13, 61234567]
)
def test_missing_or_malformed_keys_are_refused_without_echoing_the_value(bad):
    with pytest.raises(ValueError) as exc:
        k311_token(bad, KEY)
    if isinstance(bad, str) and bad.strip():
        assert bad not in str(exc.value)


def test_an_empty_secret_is_refused():
    with pytest.raises(ValueError):
        k311_token("61234567", b"")
