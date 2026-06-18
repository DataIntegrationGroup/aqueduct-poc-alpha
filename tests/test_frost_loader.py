"""Unit tests for the httpx-based FrostLoader.

A fake FROST server is wired in via ``httpx.MockTransport`` (built into httpx)
so the loader exercises real request building, JSON bodies, and response parsing
without a network or a running server. Validates idempotent upserts, id-only
links, the Data Array batch shape, watermark de-duplication, and chunking.
"""

from __future__ import annotations

import itertools
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from aqueduct_cloud_functions.adapters.hydrovu_adapter import HydroVuAdapter
from aqueduct_cloud_functions.canonical.canonical_model import (
    CanonicalDatastream,
    CanonicalObservation,
)
from aqueduct_cloud_functions.loaders.frost_loader import FrostLoader, _iso_z

_DS_ID_IN_PATH = re.compile(r"Datastreams\((\d+)\)")
_EXTERNAL_ID_IN_FILTER = re.compile(r"externalId eq '([^']*)'")

ENTITY_SETS = ["Locations", "Things", "Sensors", "ObservedProperties", "Datastreams"]


class FakeFrostServer:
    """Minimal in-memory SensorThings server for the MockTransport handler."""

    def __init__(self) -> None:
        """Start empty with id assignment beginning at 1."""
        self._ids = itertools.count(1)
        self.stores: dict[str, dict[str, int]] = {s: {} for s in ENTITY_SETS}
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.observation_batches: list[list[dict[str, Any]]] = []
        self.max_times: dict[str, str] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Route a request to the matching SensorThings behaviour."""
        path = request.url.path
        if request.method == "GET" and path.endswith("/Observations"):
            return self._observations_max(path)
        if request.method == "GET":
            return self._find(path, request)
        if request.method == "POST" and path.endswith("/CreateObservations"):
            return self._create_observations(request)
        if request.method == "POST":
            return self._create(path, request)
        return httpx.Response(405)

    def _entity_set(self, path: str) -> str:
        return path.rsplit("/", 1)[-1]

    def _find(self, path: str, request: httpx.Request) -> httpx.Response:
        entity_set = self._entity_set(path)
        match = _EXTERNAL_ID_IN_FILTER.search(request.url.params.get("$filter", ""))
        external_key = match.group(1) if match else ""
        store = self.stores[entity_set]
        value = [{"@iot.id": store[external_key]}] if external_key in store else []
        return httpx.Response(200, json={"value": value})

    def _create(self, path: str, request: httpx.Request) -> httpx.Response:
        entity_set = self._entity_set(path)
        body = json.loads(request.content)
        new_id = next(self._ids)
        self.stores[entity_set][body["properties"]["externalId"]] = new_id
        self.created.append((entity_set, body))
        location = f"http://frost.test/FROST-Server/v1.1/{entity_set}({new_id})"
        return httpx.Response(201, headers={"Location": location})

    def _create_observations(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.observation_batches.append(body)
        posted = sum(len(b["dataArray"]) for b in body)
        return httpx.Response(201, json=["created"] * posted)

    def _observations_max(self, path: str) -> httpx.Response:
        match = _DS_ID_IN_PATH.search(path)
        ds_id = match.group(1) if match else ""
        timestamp = self.max_times.get(ds_id)
        value = [{"phenomenonTime": timestamp}] if timestamp else []
        return httpx.Response(200, json={"value": value})


def _loader(server: FakeFrostServer, chunk_size: int = 1000) -> FrostLoader:
    """Build a FrostLoader whose client is backed by the fake server."""
    client = httpx.Client(transport=httpx.MockTransport(server.handle))
    return FrostLoader(
        "http://frost.test/FROST-Server", client=client, chunk_size=chunk_size
    )


def _sample_datastream() -> tuple[CanonicalDatastream, list[CanonicalObservation]]:
    """Produce one real datastream + observations from the adapter."""
    record = {
        "location_id": 123,
        "location_name": "Zumwalt Well",
        "latitude": 36.1,
        "longitude": -106.2,
        "readings": [
            {
                "parameter_id": "4",
                "unit_id": "35",
                "timestamp": 1748736000,
                "value": 10.0,
            },
            {
                "parameter_id": "4",
                "unit_id": "35",
                "timestamp": 1748739600,
                "value": 10.5,
            },
        ],
    }
    bundle = next(HydroVuAdapter([record]).run())
    datastream = bundle.datastreams[0]
    return datastream, bundle.observations[datastream.external_key]


class TestEnsureDatastream:
    """Idempotent upsert of the Location -> ... -> Datastream graph."""

    def test_creates_full_graph_in_dependency_order(self) -> None:
        """All five entities are created once, in dependency order."""
        server = FakeFrostServer()
        datastream, _ = _sample_datastream()

        ds_id = _loader(server).ensure_datastream(datastream)

        assert [entity_set for entity_set, _ in server.created] == ENTITY_SETS
        assert ds_id == "5"

    def test_external_id_written_to_properties(self) -> None:
        """Every created entity stores its canonical external_key as externalId."""
        server = FakeFrostServer()
        datastream, _ = _sample_datastream()

        _loader(server).ensure_datastream(datastream)

        by_set = {entity_set: body for entity_set, body in server.created}
        assert by_set["Locations"]["properties"]["externalId"] == "pvacd-123"
        assert by_set["Datastreams"]["properties"]["externalId"] == "pvacd-123-dtw"

    def test_links_are_id_only(self) -> None:
        """Thing links its Location, and the Datastream links all three by id."""
        server = FakeFrostServer()
        datastream, _ = _sample_datastream()

        _loader(server).ensure_datastream(datastream)

        by_set = {entity_set: body for entity_set, body in server.created}
        assert by_set["Things"]["Locations"] == [{"@iot.id": 1}]
        assert by_set["Datastreams"]["Thing"] == {"@iot.id": 2}
        assert by_set["Datastreams"]["Sensor"] == {"@iot.id": 3}
        assert by_set["Datastreams"]["ObservedProperty"] == {"@iot.id": 4}

    def test_datastream_unit_is_feet(self) -> None:
        """The created Datastream carries the feet unit of measurement."""
        server = FakeFrostServer()
        datastream, _ = _sample_datastream()

        _loader(server).ensure_datastream(datastream)

        by_set = {entity_set: body for entity_set, body in server.created}
        assert by_set["Datastreams"]["unitOfMeasurement"]["symbol"] == "ft"

    def test_idempotent_second_run_creates_nothing(self) -> None:
        """A second upsert finds existing entities and creates no duplicates."""
        server = FakeFrostServer()
        datastream, _ = _sample_datastream()
        loader = _loader(server)

        first = loader.ensure_datastream(datastream)
        second = loader.ensure_datastream(datastream)

        assert first == second
        assert len(server.created) == len(ENTITY_SETS)


class TestLoadObservations:
    """Posting observations as Data Array batches with watermark de-dup."""

    def test_posts_all_when_no_watermark(self) -> None:
        """With an empty datastream, every observation is posted."""
        server = FakeFrostServer()
        datastream, observations = _sample_datastream()

        result = _loader(server).load_observations(
            datastream.external_key, "5", observations
        )

        assert result.posted == 2
        assert result.skipped == 0
        assert len(server.observation_batches) == 1

    def test_data_array_body_shape(self) -> None:
        """The batch links the datastream by id and uses the right components."""
        server = FakeFrostServer()
        datastream, observations = _sample_datastream()

        _loader(server).load_observations(datastream.external_key, "5", observations)

        batch = server.observation_batches[0][0]
        assert batch["Datastream"] == {"@iot.id": 5}
        assert batch["components"] == ["phenomenonTime", "result"]
        assert len(batch["dataArray"]) == 2
        assert batch["dataArray"][0][0].endswith("Z")  # ISO 8601 phenomenonTime

    def test_skips_observations_at_or_before_watermark(self) -> None:
        """Only readings newer than FROST's max phenomenonTime are posted."""
        server = FakeFrostServer()
        datastream, observations = _sample_datastream()
        earliest = sorted(observations, key=lambda o: o.phenomenon_time)[0]
        server.max_times["5"] = _iso_z(earliest.phenomenon_time)

        result = _loader(server).load_observations(
            datastream.external_key, "5", observations
        )

        assert result.posted == 1
        assert result.skipped == 1

    def test_chunks_large_batches(self) -> None:
        """Observations are posted in chunks of at most chunk_size."""
        server = FakeFrostServer()
        observations = [
            CanonicalObservation(
                phenomenon_time=datetime.fromtimestamp(1748736000 + i * 3600, tz=UTC),
                result=float(i),
                datastream_external_key="pvacd-123-dtw",
            )
            for i in range(5)
        ]

        result = _loader(server, chunk_size=2).load_observations(
            "pvacd-123-dtw", "5", observations
        )

        assert result.posted == 5
        assert len(server.observation_batches) == 3  # 2 + 2 + 1
