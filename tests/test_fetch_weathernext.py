"""Tests for the WeatherNext Earth Engine worker, without network access."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pytest

from astroweather.archive import write_canonical
from astroweather.schema import FORECAST_SCHEMA
from astroweather.site import NERPIO
import astroweather.workers.fetch_weathernext as worker
from astroweather.workers.fetch_weathernext import (
    BANDS,
    COLLECTION_ID,
    NATIVE_CRS,
    NATIVE_TRANSFORM,
    _rows,
    derive_rh2m,
    derive_wind_direction,
    fetch_region,
    latest_complete_init,
    native_cell,
    parse_image_id,
    records_from_region,
    run,
    select_complete_init,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UTC = timezone.utc


@pytest.fixture
def fixture_records() -> list[dict]:
    return json.loads(
        (FIXTURES / "weathernext_records.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def ee_region() -> list[list]:
    return json.loads(
        (FIXTURES / "weathernext_ee_region.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def retrieved_at() -> datetime:
    return datetime(2026, 9, 7, 22, 0, 0, tzinfo=UTC)


def test_rows_count_and_models(fixture_records, retrieved_at):
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    assert len(rows) == 36

    expected_models = {
        "weathernext3",
        "weathernext3_p10",
        "weathernext3_p25",
        "weathernext3_p50",
        "weathernext3_p75",
        "weathernext3_p90",
    }
    assert {row["model"] for row in rows} == expected_models

    for model in expected_models:
        model_rows = [row for row in rows if row["model"] == model]
        assert len(model_rows) == 6
        assert all(row["member"] is None for row in model_rows)


def test_rows_column_names(fixture_records, retrieved_at):
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    schema_names = set(FORECAST_SCHEMA.names)
    for index, row in enumerate(rows):
        assert not set(row) - schema_names, index


def test_unit_conversions(fixture_records, retrieved_at):
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    row = next(
        item
        for item in rows
        if item["model"] == "weathernext3" and item["lead_h"] == 1
    )
    assert row["cloud_total"] == 42.0
    assert row["t2m"] == 10.0
    assert row["precipitation"] == 1.2
    assert row["pressure_msl"] == round(1013.25, 1)
    assert 0.0 <= row["rh2m"] <= 100.0
    assert 0.0 <= row["wind_dir10m"] < 360.0


def test_lead_h_invariant_and_manipulated_fixture(
    fixture_records,
    retrieved_at,
):
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    for row in rows:
        expected = int(
            (row["valid_utc"] - row["run_utc"]).total_seconds() // 3600
        )
        assert row["lead_h"] == expected

    manipulated = copy.deepcopy(fixture_records)
    manipulated[0]["forecast"][0]["hours"] = 99
    with pytest.raises(ValueError, match="Incoherent lead_h"):
        _rows(manipulated, NERPIO, retrieved_at)


def test_write_canonical_integration(
    fixture_records,
    retrieved_at,
    tmp_path,
):
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    output = write_canonical(
        rows,
        dataset="forecasts",
        site_id=NERPIO.site_id,
        root=tmp_path,
    )
    assert output is not None
    assert output.exists()


def test_derived_fields_only_for_the_mean(
    fixture_records,
    retrieved_at,
):
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    for row in rows:
        if row["model"] == "weathernext3":
            assert "wind_dir10m" in row
            assert "rh2m" in row
        else:
            assert "wind_dir10m" not in row
            assert "rh2m" not in row


def test_provenance_does_not_borrow_openmeteo_cell(
    fixture_records,
    retrieved_at,
):
    row = _rows(fixture_records, NERPIO, retrieved_at)[0]
    assert row["model_grid_elevation"] is None
    assert row["downscaling_elevation"] is None
    assert row["license_tag"] == "weathernext3-gdm-experimental"


def test_wind_direction_convention():
    assert derive_wind_direction(3.0, 0.0) == 270.0
    assert derive_wind_direction(0.0, -3.0) == 0.0
    assert derive_rh2m(10.0, 10.0) == 100.0


def test_native_cell_nerpio_and_requested_edges():
    assert native_cell(NERPIO.latitude, NERPIO.longitude) == (38.2, -2.3)
    assert native_cell(38.25, -2.35) == (38.2, -2.3)

    assert native_cell(90.0, 0.0)[0] == 90.0
    assert native_cell(-90.0, 0.0)[0] == -90.0

    assert native_cell(0.0, 180.0)[1] == -180.0
    assert native_cell(0.0, 179.95)[1] == -180.0
    assert native_cell(0.0, math.nextafter(180.0, 0.0))[1] == -180.0

    assert native_cell(0.0, -180.0) == native_cell(0.0, 180.0)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (math.nan, 0.0),
        (0.0, math.nan),
        (90.000001, 0.0),
        (-90.000001, 0.0),
    ],
)
def test_native_cell_rejects_invalid_coordinates(lat, lon):
    with pytest.raises(ValueError):
        native_cell(lat, lon)


def test_parse_image_id_with_and_without_prefix():
    expected = (
        datetime(2026, 9, 15, 0, tzinfo=UTC),
        datetime(2026, 9, 15, 1, tzinfo=UTC),
    )
    assert parse_image_id("202609150000_202609150100") == expected
    assert parse_image_id(
        "projects/example/assets/202609150000_202609150100"
    ) == expected


@pytest.mark.parametrize(
    "image_id",
    [
        "202609150000-202609150100",
        "202602300000_202603010100",
        "202609150100_202609150000",
    ],
)
def test_parse_image_id_rejects_bad_ids(image_id):
    with pytest.raises(ValueError):
        parse_image_id(image_id)


def test_real_region_to_records_and_rows(ee_region, retrieved_at):
    records = records_from_region(
        ee_region,
        NERPIO,
        expected_init=datetime(2026, 9, 15, 0, tzinfo=UTC),
    )
    assert len(records) == 1
    assert [item["hours"] for item in records[0]["forecast"]] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]

    rows = _rows(records, NERPIO, retrieved_at)
    assert len(rows) == 36

    lead_one = next(
        row
        for row in rows
        if row["model"] == "weathernext3" and row["lead_h"] == 1
    )
    assert lead_one["valid_utc"] == datetime(
        2026, 9, 15, 1, tzinfo=UTC
    )

    header = ee_region[0]
    raw_time = ee_region[1][header.index("time")]
    region_time = datetime.fromtimestamp(raw_time / 1000, tz=UTC)
    assert lead_one["valid_utc"] != region_time


def test_records_rejects_valid_time_in_time_column(ee_region):
    manipulated = copy.deepcopy(ee_region)
    manipulated[1][3] = int(
        datetime(2026, 9, 15, 1, tzinfo=UTC).timestamp() * 1000
    )
    with pytest.raises(ValueError, match="init"):
        records_from_region(manipulated, NERPIO)


def test_records_rejects_scale_resampled_pixel(ee_region):
    manipulated = copy.deepcopy(ee_region)
    for row in manipulated[1:]:
        row[1] = -2.35010
        row[2] = 38.15017
    with pytest.raises(ValueError, match="pixel"):
        records_from_region(manipulated, NERPIO)


def test_records_sort_disordered_table(ee_region):
    manipulated = [ee_region[0], *reversed(ee_region[1:])]
    records = records_from_region(manipulated, NERPIO)
    assert [item["hours"] for item in records[0]["forecast"]] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]


def test_records_reject_duplicate_id(ee_region):
    manipulated = copy.deepcopy(ee_region)
    manipulated[2][0] = manipulated[1][0]
    with pytest.raises(ValueError, match="repetido"):
        records_from_region(manipulated, NERPIO)


def test_none_band_is_absent_from_item_and_canonical_row(
    ee_region,
    retrieved_at,
):
    manipulated = copy.deepcopy(ee_region)
    cloud_index = manipulated[0].index("total_cloud_cover_mean")
    manipulated[1][cloud_index] = None

    records = records_from_region(manipulated, NERPIO)
    first_item = records[0]["forecast"][0]
    assert "total_cloud_cover_mean" not in first_item

    rows = _rows(records, NERPIO, retrieved_at)
    canonical = next(
        row
        for row in rows
        if row["model"] == "weathernext3" and row["lead_h"] == 1
    )
    assert "cloud_total" not in canonical


def test_records_header_only_returns_empty(ee_region):
    assert records_from_region([ee_region[0]], NERPIO) == []


def test_select_complete_init_skips_latest_partial():
    counts = {
        "2026-09-17T01:00:00Z": 48,
        "2026-09-17T02:00:00Z": 48,
        "2026-09-17T03:00:00Z": 17,
    }
    assert select_complete_init(counts, 48) == datetime(
        2026, 9, 17, 2, tzinfo=UTC
    )


def test_select_complete_init_excludes_48_hour_run_for_72_hours():
    counts = {
        "2026-09-17T00:00:00Z": 72,
        "2026-09-17T01:00:00Z": 48,
    }
    assert select_complete_init(counts, 72) == datetime(
        2026, 9, 17, 0, tzinfo=UTC
    )


def test_select_complete_init_empty():
    assert select_complete_init({}, 48) is None


@pytest.mark.parametrize("hours", [0, 361])
def test_select_complete_init_rejects_hours_outside_range(hours):
    with pytest.raises(ValueError):
        select_complete_init({}, hours)


class FakeInfo:
    def __init__(self, value):
        self.value = value

    def getInfo(self):
        return self.value


class FakeCollection:
    def __init__(self, ee):
        self.ee = ee

    def filter(self, expression):
        self.ee.calls.append(("filter", expression))
        return self

    def filterDate(self, start, end):
        self.ee.calls.append(("filterDate", start, end))
        return self

    def select(self, bands):
        self.ee.calls.append(("select", bands))
        return self

    def getRegion(self, **kwargs):
        self.ee.calls.append(("getRegion", kwargs))
        return FakeInfo(self.ee.region)

    def aggregate_histogram(self, property_name):
        self.ee.calls.append(("aggregate_histogram", property_name))
        return FakeInfo(self.ee.histogram)


class FakeFilter:
    def __init__(self, ee):
        self.ee = ee

    def eq(self, property_name, value):
        expression = ("eq", property_name, value)
        self.ee.calls.append(("Filter.eq", property_name, value))
        return expression

    def lte(self, property_name, value):
        expression = ("lte", property_name, value)
        self.ee.calls.append(("Filter.lte", property_name, value))
        return expression


class FakeGeometry:
    def __init__(self, ee):
        self.ee = ee

    def Point(self, coordinates):
        point = ("Point", coordinates)
        self.ee.calls.append(("Geometry.Point", coordinates))
        return point


class FakeEE:
    def __init__(self, region=None, histogram=None):
        self.region = region or []
        self.histogram = histogram or {}
        self.calls = []
        self.Filter = FakeFilter(self)
        self.Geometry = FakeGeometry(self)
        self.ServiceAccountCredentials = lambda *a, **k: ("sa", a, k)

    def Initialize(self, credentials=None, **kwargs):
        self.calls.append(("Initialize", kwargs))
        self.credentials = credentials

    def ImageCollection(self, collection_id):
        self.calls.append(("ImageCollection", collection_id))
        return FakeCollection(self)


def test_fetch_region_uses_native_projection_and_no_scale(ee_region):
    ee = FakeEE(region=ee_region)
    init = datetime(2026, 9, 15, 0, tzinfo=UTC)

    assert fetch_region(ee, NERPIO, init, 6) == ee_region

    assert (
        "Filter.eq",
        "start_time",
        "2026-09-15T00:00:00Z",
    ) in ee.calls
    assert ("Filter.lte", "forecast_hour", 6) in ee.calls
    assert ("select", list(BANDS)) in ee.calls

    get_region = next(
        call[1] for call in ee.calls if call[0] == "getRegion"
    )
    assert get_region["crs"] == NATIVE_CRS
    assert get_region["crsTransform"] == list(NATIVE_TRANSFORM)
    assert "scale" not in get_region
    assert get_region["geometry"] == (
        "Point",
        [NERPIO.longitude, NERPIO.latitude],
    )


def test_latest_complete_init_builds_histogram_query():
    ee = FakeEE(
        histogram={
            "2026-09-17T01:00:00Z": 48,
            "2026-09-17T02:00:00Z": 48,
            "2026-09-17T03:00:00Z": 12,
        }
    )
    selected = latest_complete_init(
        ee,
        48,
        datetime(2026, 9, 17, 9, tzinfo=UTC),
        lookback_h=30,
    )
    assert selected == datetime(2026, 9, 17, 2, tzinfo=UTC)
    assert (
        "filterDate",
        "2026-09-16T03:00:00Z",
        "2026-09-17T10:00:00Z",
    ) in ee.calls
    assert ("Filter.lte", "forecast_hour", 48) in ee.calls
    assert ("aggregate_histogram", "start_time") in ee.calls


def test_run_missing_project_raises_before_import(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(RuntimeError, match="Faltan variables de entorno"):
        run()


def test_run_with_fake_ee_archives_and_writes(
    monkeypatch,
    ee_region,
):
    ee = FakeEE(region=ee_region)
    monkeypatch.setattr(worker, "credentials_source", lambda: "user")
    stored = []
    written = []

    monkeypatch.setattr(
        worker,
        "store_raw",
        lambda payload, **kwargs: stored.append((payload, kwargs)),
    )
    monkeypatch.setattr(
        worker,
        "write_canonical",
        lambda rows, **kwargs: written.append((rows, kwargs)),
    )
    retrieved = datetime(2026, 9, 17, 9, tzinfo=UTC)
    monkeypatch.setattr(worker, "utcnow", lambda: retrieved)

    counts = run(
        hours=6,
        init_time=datetime(2026, 9, 15, 0, tzinfo=UTC),
        project="astroweather-cabra",
        ee_module=ee,
    )

    assert ee.calls[0] == (
        "Initialize",
        {"project": "astroweather-cabra"},
    )
    assert counts == {
        "weathernext3": 6,
        "weathernext3_p10": 6,
        "weathernext3_p25": 6,
        "weathernext3_p50": 6,
        "weathernext3_p75": 6,
        "weathernext3_p90": 6,
    }

    assert len(stored) == 1
    payload, raw_kwargs = stored[0]
    assert raw_kwargs["source"] == "weathernext_ee"
    assert raw_kwargs["site_id"] == NERPIO.site_id
    assert payload["collection"] == COLLECTION_ID
    assert payload["init_time"] == "2026-09-15T00:00:00Z"
    assert payload["hours"] == 6
    assert payload["crs"] == NATIVE_CRS
    assert payload["crs_transform"] == list(NATIVE_TRANSFORM)
    assert payload["region"] == ee_region

    assert len(written) == 1
    rows, write_kwargs = written[0]
    assert len(rows) == 36
    assert write_kwargs == {
        "dataset": "forecasts",
        "site_id": NERPIO.site_id,
        "tag": "weathernext3",
    }


def test_run_refuses_incomplete_initialization(
    monkeypatch,
    ee_region,
):
    ee = FakeEE(region=ee_region)
    monkeypatch.setattr(worker, "credentials_source", lambda: "user")
    store_calls = []
    write_calls = []

    monkeypatch.setattr(
        worker,
        "store_raw",
        lambda *args, **kwargs: store_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        worker,
        "write_canonical",
        lambda *args, **kwargs: write_calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="incompleta"):
        run(
            hours=8,
            init_time=datetime(2026, 9, 15, 0, tzinfo=UTC),
            project="astroweather-cabra",
            ee_module=ee,
        )

    assert store_calls == []
    assert write_calls == []


def test_canonical_rows_are_rounded_to_published_precision(ee_region, retrieved_at):
    """The model's float noise does not compress: 0.0772 % is stored as 0.1."""
    rows = _rows(records_from_region(ee_region, NERPIO), NERPIO, retrieved_at)
    lead_one = next(r for r in rows if r["model"] == "weathernext3" and r["lead_h"] == 1)
    assert lead_one["cloud_total"] == 0.1
    for row in rows:
        for column, decimals in worker.DECIMALS.items():
            value = row.get(column)
            if value is not None:
                assert value == round(value, decimals), (column, value)


def test_initialize_prefers_the_service_account_key(monkeypatch, tmp_path):
    """The runner will pass the key as an environment variable, not a file."""
    key = json.dumps({"client_email": "runner@astroweather-cabra.iam.gserviceaccount.com"})
    monkeypatch.setenv(worker.SERVICE_ACCOUNT_ENV, key)
    ee = FakeEE()
    ee.ServiceAccountCredentials = lambda email, key_file=None, key_data=None: (
        "sa", email, key_file, key_data)
    assert worker.initialize(ee, "astroweather-cabra") == "service_account"
    init = next(c for c in ee.calls if c[0] == "Initialize")
    assert init[1] == {"project": "astroweather-cabra"}
    assert ee.credentials[0] == "sa"        # went in as credentials, not as env

    path = tmp_path / "key.json"
    path.write_text(key, encoding="utf-8")
    monkeypatch.setenv(worker.SERVICE_ACCOUNT_ENV, str(path))
    assert worker.initialize(FakeEE(), "astroweather-cabra") == "service_account"


def test_without_credentials_it_says_so_instead_of_failing_obscurely(monkeypatch, tmp_path):
    """On the GitHub runner there are none: the job must skip, not crash blind."""
    monkeypatch.delenv(worker.SERVICE_ACCOUNT_ENV, raising=False)
    monkeypatch.setattr(worker, "USER_CREDENTIALS", tmp_path / "nope")
    assert worker.credentials_source() is None
    with pytest.raises(RuntimeError, match="sin credenciales"):
        worker.initialize(FakeEE(), "astroweather-cabra")


def test_only_the_runs_the_exam_scored(monkeypatch):
    """00Z and 12Z: archiving 06Z too would score what was not compared."""
    counts = {"2026-09-17T00:00:00Z": 144, "2026-09-17T06:00:00Z": 144,
              "2026-09-17T12:00:00Z": 144}
    assert select_complete_init(counts, 144) == datetime(2026, 9, 17, 12, tzinfo=UTC)
    assert select_complete_init(counts, 144, allowed_hours=(0, 12)) == datetime(
        2026, 9, 17, 12, tzinfo=UTC)
    del counts["2026-09-17T12:00:00Z"]
    assert select_complete_init(counts, 144, allowed_hours=(0, 12)) == datetime(
        2026, 9, 17, 0, tzinfo=UTC)
