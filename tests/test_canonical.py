"""Smoke tests for the canonical model package.

These test the canonical contract that every source adapter builds on.
"""

from __future__ import annotations

import pytest

from aqueduct_cloud_functions.adapters import CabqAdapter, HydroVuAdapter
from aqueduct_cloud_functions.canonical import (
    CanonicalLocation,
    CanonicalThing,
    make_datastream_key,
    make_location_key,
)


def test_make_location_key_lowercases_agency() -> None:
    """The location key joins a lowercased agency code with the source id."""
    assert make_location_key("PVACD", "123") == "pvacd-123"


def test_make_datastream_key_appends_suffix() -> None:
    """The datastream key extends the location key with a property suffix."""
    assert make_datastream_key("PVACD", "123", "dtw") == "pvacd-123-dtw"


def test_canonical_thing_carries_location_and_agency() -> None:
    """A CanonicalThing nests its Location and records the agency property."""
    location = CanonicalLocation(
        external_key="pvacd-123",
        name="Zumwalt Well",
        description="",
        geometry={"type": "Point", "coordinates": [-106.2, 36.1, 1645.9]},
    )
    thing = CanonicalThing(
        external_key="pvacd-123",
        name="Zumwalt Well",
        description="",
        location=location,
        properties={"agency": "PVACD"},
    )
    assert thing.location.external_key == "pvacd-123"
    assert thing.properties["agency"] == "PVACD"


def test_adapters_set_their_agency_codes() -> None:
    """Each adapter initializes with its uppercased agency code."""
    assert HydroVuAdapter().agency == "PVACD"
    assert CabqAdapter().agency == "CABQ"


def test_adapter_stub_methods_raise_not_implemented() -> None:
    """Unimplemented adapter mapping methods fail loudly, not silently."""
    with pytest.raises(NotImplementedError):
        HydroVuAdapter().to_thing({})
