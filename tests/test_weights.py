"""Unit tests for equal-share redistribution with pins (no network)."""

import pytest

from app.services.weights import normalize_to_100, redistribute


def _shares(offers: list[dict]) -> dict[int, float]:
    return {o["id"]: o["share"] for o in offers}


def test_equal_split_two_active():
    offers = [
        {"id": 1, "share": 100, "pinned": False, "status": "active"},
        {"id": 2, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 50, 2: 50}
    assert sum(o["share"] for o in offers) == 100


def test_equal_split_three_active():
    offers = [
        {"id": 1, "share": 100, "pinned": False, "status": "active"},
        {"id": 2, "share": 0, "pinned": False, "status": "active"},
        {"id": 3, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 34, 2: 33, 3: 33}


def test_removed_get_zero_and_rest_split():
    offers = [
        {"id": 1, "share": 50, "pinned": False, "status": "active"},
        {"id": 2, "share": 50, "pinned": False, "status": "removed"},
        {"id": 3, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert offers[1]["share"] == 0
    assert offers[0]["share"] == 50
    assert offers[2]["share"] == 50


def test_pinned_kept_free_split_remainder():
    offers = [
        {"id": 1, "share": 40, "pinned": True, "status": "active"},
        {"id": 2, "share": 0, "pinned": False, "status": "active"},
        {"id": 3, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert offers[0]["share"] == 40
    assert offers[1]["share"] == 30
    assert offers[2]["share"] == 30
    assert sum(o["share"] for o in offers) == 100


def test_only_pinned_normalized_to_100():
    offers = [
        {"id": 1, "share": 60, "pinned": True, "status": "active"},
        {"id": 2, "share": 20, "pinned": True, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 75, 2: 25}


def test_pinned_over_100_clamped():
    offers = [
        {"id": 1, "share": 80, "pinned": True, "status": "active"},
        {"id": 2, "share": 80, "pinned": True, "status": "active"},
        {"id": 3, "share": 10, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 50, 2: 50, 3: 0}


def test_pinned_slightly_over_100_never_sums_to_101():
    # Old proportional clamp rounded 50/50/1 (sum 101) to 50+50+1 = 101.
    offers = [
        {"id": 1, "share": 50, "pinned": True, "status": "active"},
        {"id": 2, "share": 50, "pinned": True, "status": "active"},
        {"id": 3, "share": 1, "pinned": True, "status": "active"},
        {"id": 4, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 50, 2: 49, 3: 1, 4: 0}


def test_pinned_thirds_over_100_sum_exactly_100():
    offers = [
        {"id": 1, "share": 60, "pinned": True, "status": "active"},
        {"id": 2, "share": 60, "pinned": True, "status": "active"},
        {"id": 3, "share": 60, "pinned": True, "status": "active"},
        {"id": 4, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 34, 2: 33, 3: 33, 4: 0}


def test_fractional_pin_rounded_to_integer():
    offers = [
        {"id": 1, "share": 33.3, "pinned": True, "status": "active"},
        {"id": 2, "share": 0, "pinned": False, "status": "active"},
        {"id": 3, "share": 0, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert _shares(offers) == {1: 33, 2: 34, 3: 33}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], []),
        ([100], [100]),
        ([0, 0, 0], [34, 33, 33]),
        ([33.4, 33.3, 33.3], [34, 33, 33]),
        ([1, 1, 1], [34, 33, 33]),
        ([50, 50, 1], [50, 49, 1]),
        ([40, 30, 30], [40, 30, 30]),
        ([10, 10], [50, 50]),
        ([-5, 10], [0, 100]),
    ],
)
def test_normalize_to_100(values, expected):
    result = normalize_to_100(values)
    assert result == expected
    assert all(isinstance(v, int) for v in result)
    if values:
        assert sum(result) == 100


def test_no_active_offers():
    offers = [
        {"id": 1, "share": 50, "pinned": False, "status": "removed"},
        {"id": 2, "share": 50, "pinned": False, "status": "removed"},
    ]
    redistribute(offers)
    assert all(o["share"] == 0 for o in offers)


def test_single_active_gets_100():
    offers = [
        {"id": 1, "share": 0, "pinned": False, "status": "active"},
        {"id": 2, "share": 100, "pinned": False, "status": "removed"},
    ]
    redistribute(offers)
    assert offers[0]["share"] == 100
    assert offers[1]["share"] == 0


def test_pinned_at_100_free_get_zero():
    offers = [
        {"id": 1, "share": 100, "pinned": True, "status": "active"},
        {"id": 2, "share": 50, "pinned": False, "status": "active"},
    ]
    redistribute(offers)
    assert offers[0]["share"] == 100
    assert offers[1]["share"] == 0
