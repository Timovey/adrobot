"""Unit tests for naming / alias helpers (no network)."""

from app.services.campaigns import ensure_prefixed_name
from app.services.keitaro import slugify_alias


def test_ensure_prefixed_adds_prefix():
    assert ensure_prefixed_name("my_camp", "test_timovey_") == "test_timovey_my_camp"


def test_ensure_prefixed_keeps_existing_prefix():
    assert ensure_prefixed_name("test_timovey_x", "test_timovey_") == "test_timovey_x"


def test_ensure_prefixed_keeps_generic_test_prefix():
    assert ensure_prefixed_name("test_other", "test_timovey_") == "test_other"


def test_ensure_prefixed_strips_whitespace():
    assert ensure_prefixed_name("  foo  ", "test_timovey_") == "test_timovey_foo"


def test_slugify_alias_is_url_safe_and_unique():
    a = slugify_alias("Hello World!!")
    b = slugify_alias("Hello World!!")
    assert a.startswith("hello-world")
    assert "-" in a
    # UUID suffix makes aliases unique even for same name
    assert a != b
    assert all(c.isalnum() or c == "-" for c in a)
