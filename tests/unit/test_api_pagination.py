from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tracecause.api.errors import ApiError
from tracecause.api.pagination import decode_cursor, encode_cursor


def test_cursor_roundtrip_is_bound_to_collection_and_filter():
    instant, identifier = datetime.now(UTC), uuid4()
    value = encode_cursor("episodes:workspace:synthetic", instant, identifier)
    assert decode_cursor(value, "episodes:workspace:synthetic") == (instant, identifier)
    with pytest.raises(ApiError):
        decode_cursor(value, "episodes:workspace:captured_demo")


@pytest.mark.parametrize("value", ["???", "x" * 1001, "W10", "bnVsbA"])
def test_invalid_cursors_fail_safely(value):
    with pytest.raises(ApiError):
        decode_cursor(value, "episodes")
