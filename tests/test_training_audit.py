from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pandas as pd

from usgs_nwis.config import load_config
from usgs_nwis.training_audit import (
    _qualifier_tokens,
    _runs,
    _station_metrics,
    _tier_eligibility,
    aggregate_site_hourly,
    download_training,
    parse_training_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


def _cfg():
    return load_config(ROOT / "configs" / "training_audit.yaml")


def _raw_rows() -> pd.DataFrame:
    rows = []
    for parameter, values, qualifiers in (
        ("00060", [1.0, 2.0, 3.0, 4.0], ["A", "A", "A", "A"]),
        ("00095", [100.0, 110.0, 120.0, 130.0], ["A", "A", "P", "A"]),
    ):
        for time, value, qualifier in zip(
            pd.date_range("2020-01-01T00:00:00Z", periods=4, freq="15min"),
            values,
            qualifiers,
            strict=True,
        ):
            rows.append(
                {
                    "site_no": "01234567",
                    "param_cd": parameter,
                    "series_key": f"{parameter}|method",
                    "series_name": parameter,
                    "method": "method",
                    "time": time,
                    "value": value,
                    "qualifiers": qualifier,
                }
            )
    return pd.DataFrame(rows)


def test_hourly_aggregation_is_utc_and_never_interpolates() -> None:
    hourly = aggregate_site_hourly(_raw_rows(), _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    assert str(hourly["time"].dt.tz) == "UTC"
    assert first["q_value"] == 2.5
    assert first["q_native_samples"] == 4
    assert first["q_hour_coverage"] == 1.0
    assert first["q_valid"]
    assert not hourly["was_interpolated"].any()
    assert not hourly["was_forward_filled"].any()


def test_primary_mask_rejects_any_provisional_qualifier() -> None:
    frame = _raw_rows()
    sc = frame["param_cd"].eq("00095")
    frame.loc[sc, "qualifiers"] = ["A", "A|e", "A|P", "P"]
    hourly = aggregate_site_hourly(frame, _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    assert first["sc_approved_samples"] == 2
    assert first["sc_estimated_samples"] == 1
    assert first["sc_approved_unique_samples"] == 2
    assert first["sc_sensitivity_samples"] == 2
    assert not first["sc_valid"]
    assert not first["sc_valid_sensitivity"]
    assert not first["paired_valid"]
    assert not first["paired_valid_sensitivity"]


def test_qualifier_tokenization_does_not_use_substrings() -> None:
    assert _qualifier_tokens("A") == frozenset({"A"})
    assert _qualifier_tokens("A,e") == frozenset({"A", "e"})
    assert _qualifier_tokens("Ae") == frozenset({"A", "e"})
    assert _qualifier_tokens("A|P") == frozenset({"A", "P"})
    assert _qualifier_tokens("ICE") == frozenset({"ICE"})


def test_sc_zero_is_invalid_and_duplicates_do_not_inflate_coverage() -> None:
    frame = _raw_rows()
    frame.loc[frame["param_cd"].eq("00095"), "value"] = [0.0, 100.0, 110.0, 120.0]
    duplicate = frame[frame["param_cd"].eq("00095")].iloc[[1]].copy()
    frame = pd.concat([frame, duplicate], ignore_index=True)
    hourly = aggregate_site_hourly(frame, _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    assert first["sc_nonpositive_samples"] == 1
    assert first["sc_native_samples"] == 5
    assert first["sc_approved_unique_samples"] == 2
    assert first["sc_hour_coverage"] == 0.5
    assert not first["sc_valid"]


def test_split_metrics_use_fixed_windows_and_valid_day_year_rules() -> None:
    cfg = _cfg()
    cfg.screening["valid_day_min_paired_hours"] = 12
    cfg.screening["valid_year_min_valid_days"] = {"cold": 1, "warm": 1}
    times = pd.concat(
        [
            pd.Series(pd.date_range("2015-01-01", periods=24, freq="h", tz="UTC")),
            pd.Series(pd.date_range("2015-06-01", periods=24, freq="h", tz="UTC")),
            pd.Series(pd.date_range("2020-01-01", periods=24, freq="h", tz="UTC")),
            pd.Series(pd.date_range("2020-06-01", periods=24, freq="h", tz="UTC")),
            pd.Series(pd.date_range("2022-01-01", periods=24, freq="h", tz="UTC")),
            pd.Series(pd.date_range("2022-06-01", periods=24, freq="h", tz="UTC")),
        ],
        ignore_index=True,
    )
    hourly = pd.DataFrame(
        {
            "time": times,
            "q_valid": True,
            "sc_valid": True,
            "paired_valid": True,
            "paired_valid_sensitivity": True,
            "q_value": range(len(times)),
            "sc_value": 100.0,
            "q_quality_flags": "A",
            "sc_quality_flags": "A",
            "q_native_samples": 1,
            "sc_native_samples": 1,
            "q_approved_samples": 1,
            "sc_approved_samples": 1,
            "q_estimated_samples": 0,
            "sc_estimated_samples": 0,
            "q_duplicate_timestamps": 0,
            "sc_duplicate_timestamps": 0,
            "q_negative_samples": 0,
            "sc_negative_samples": 0,
            "q_nonpositive_samples": 0,
            "sc_nonpositive_samples": 0,
            "q_range_failure_samples": 0,
            "sc_range_failure_samples": 0,
            "q_native_cadence_minutes": 60.0,
            "sc_native_cadence_minutes": 60.0,
        }
    )
    metrics = _station_metrics(hourly, cfg)
    assert metrics["train_valid_days"] == 2
    assert metrics["validation_valid_days"] == 2
    assert metrics["test_valid_days"] == 2
    assert metrics["train_valid_years"] == 1
    assert metrics["validation_valid_years"] == 1
    assert metrics["test_valid_years"] == 1


def test_duplicate_timestamps_are_reported_not_silently_dropped() -> None:
    frame = pd.concat([_raw_rows(), _raw_rows().iloc[[0]]], ignore_index=True)
    hourly = aggregate_site_hourly(frame, _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    assert first["q_duplicate_timestamps"] == 1
    assert first["q_native_samples"] == 5


def test_gap_run_statistics() -> None:
    mask = pd.Series([False, True, True, False, False, False, True])
    assert _runs(mask) == [2, 1]
    assert _runs(~mask) == [1, 3]


def test_utc_conversion_from_explicit_offsets() -> None:
    frame = _raw_rows()
    frame.loc[0, "time"] = "2020-01-01T00:00:00-05:00"
    hourly = aggregate_site_hourly(frame, _cfg())
    assert pd.Timestamp("2020-01-01T05:00:00Z") in set(hourly["time"])


def test_catalog_union_keeps_q_only_sc_only_and_paired(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    text = (ROOT / "configs" / "training_audit.yaml").read_text(encoding="utf-8")
    text = re.sub(r"states: \[[^\n]+\]", "states: [AL]", text)
    config_path = config_dir / "training_audit.yaml"
    config_path.write_text(text, encoding="utf-8")
    raw = tmp_path / "data" / "raw" / "catalog"
    raw.mkdir(parents=True)
    header = "\t".join(
        [
            "agency_cd",
            "site_no",
            "station_nm",
            "site_tp_cd",
            "dec_lat_va",
            "dec_long_va",
            "coord_acy_cd",
            "dec_coord_datum_cd",
            "alt_va",
            "alt_acy_va",
            "alt_datum_cd",
            "huc_cd",
            "data_type_cd",
            "parm_cd",
            "stat_cd",
            "ts_id",
            "loc_web_ds",
            "medium_grp_cd",
            "parm_grp_cd",
            "srs_id",
            "access_cd",
            "begin_date",
            "end_date",
            "count_nu",
        ]
    )
    types = "\t".join(["10s"] * 24)
    base = [
        "USGS",
        "",
        "station",
        "ST",
        "35",
        "-86",
        "",
        "",
        "",
        "",
        "",
        "06010201",
        "uv",
        "",
        "",
        "1",
        "",
        "",
        "",
        "",
        "",
        "2010-01-01",
        "2024-12-31",
        "100",
    ]
    records = []
    for site, parameter in (
        ("01000001", "00060"),
        ("01000002", "00095"),
        ("01000003", "00060"),
        ("01000003", "00095"),
    ):
        row = base.copy()
        row[1] = site
        row[13] = parameter
        records.append("\t".join(row))
    with gzip.open(raw / "AL.rdb.gz", "wb") as handle:
        handle.write(("\n".join([header, types, *records]) + "\n").encode("latin-1"))
    catalog = parse_training_catalog(load_config(config_path)).set_index("site_no")
    assert len(catalog) == 3
    assert bool(catalog.loc["01000001", "catalog_q"])
    assert not bool(catalog.loc["01000001", "catalog_sc"])
    assert bool(catalog.loc["01000003", "catalog_paired"])
    assert bool(catalog.loc["01000003", "window_overlap"])


def _temp_config(tmp_path: Path, sites: tuple[str, ...] = ("01000001",)):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    text = (ROOT / "configs" / "training_audit.yaml").read_text(encoding="utf-8")
    config_path = config_dir / "training_audit.yaml"
    config_path.write_text(text, encoding="utf-8")
    interim = tmp_path / "data" / "interim" / "training_audit"
    interim.mkdir(parents=True)
    pd.DataFrame({"site_no": list(sites), "window_overlap": [True] * len(sites)}).to_parquet(
        interim / "catalog.parquet", index=False
    )
    return load_config(config_path)


def _no_network(*args, **kwargs):
    raise AssertionError("network must not be used")


def _eligible_metrics(**overrides):
    metrics = {
        "train_valid_years": 3,
        "validation_valid_years": 1,
        "test_valid_years": 2,
        "train_paired_valid_hours": 12000,
        "validation_paired_valid_hours": 2000,
        "test_paired_valid_hours": 2000,
        "train_q_coverage_by_year_json": json.dumps(
            {str(year): {"q_coverage": 0.85} for year in range(2015, 2020)}
        ),
        "validation_q_coverage_by_year_json": json.dumps({"2020": {"q_coverage": 0.75}}),
        "test_q_coverage_by_year_json": json.dumps(
            {"2022": {"q_coverage": 0.75}, "2023": {"q_coverage": 0.75}}
        ),
        "all_paired_hours": 20000,
        "all_low_state_paired_hours": 6000,
        "all_mid_state_paired_hours": 8000,
        "all_high_state_paired_hours": 6000,
        "all_rising_phase_paired_hours": 5000,
        "all_falling_phase_paired_hours": 5000,
        "all_stable_phase_paired_hours": 10000,
    }
    metrics.update(overrides)
    return metrics


def test_approved_estimated_values_are_accepted_and_flagged() -> None:
    frame = _raw_rows()
    frame.loc[frame["param_cd"].eq("00095"), "qualifiers"] = ["A|e"] * 4
    hourly = aggregate_site_hourly(frame, _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    assert first["sc_estimated_samples"] == 4
    assert first["sc_valid"]
    assert first["paired_valid"]


def test_hourly_coverage_boundary_is_seventy_five_percent() -> None:
    rows = []
    for index, qualifier in enumerate(["A", "A", "A", "P", "A", "A", "P", "P"]):
        rows.append(
            {
                "site_no": "01234567",
                "param_cd": "00060",
                "series_key": "00060|method",
                "series_name": "00060",
                "method": "method",
                "time": pd.Timestamp("2020-01-01T00:00:00Z") + pd.Timedelta(minutes=15 * index),
                "value": 1.0,
                "qualifiers": qualifier,
            }
        )
    hourly = aggregate_site_hourly(pd.DataFrame(rows), _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    second = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T01:00:00Z")].iloc[0]
    assert first["q_hour_coverage"] == 0.75
    assert first["q_valid"]
    assert second["q_hour_coverage"] == 0.5
    assert not second["q_valid"]


def test_core_and_extended_tier_thresholds() -> None:
    cfg = _cfg()
    assert _tier_eligibility(_eligible_metrics(), cfg, "core") == (True, [])
    assert _tier_eligibility(_eligible_metrics(), cfg, "extended") == (True, [])
    short_train = _eligible_metrics(train_valid_years=2)
    assert not _tier_eligibility(short_train, cfg, "extended")[0]
    fewer_hours = _eligible_metrics(train_paired_valid_hours=6000)
    assert not _tier_eligibility(fewer_hours, cfg, "core")[0]
    assert _tier_eligibility(fewer_hours, cfg, "extended")[0]
    low_state = _eligible_metrics(all_low_state_paired_hours=100)
    eligible, reasons = _tier_eligibility(low_state, cfg, "core")
    assert not eligible
    assert "low_state_coverage" in reasons


def test_cached_site_year_is_not_requested_again(tmp_path: Path, monkeypatch) -> None:
    cfg = _temp_config(tmp_path)
    raw = tmp_path / "data" / "raw" / "training_audit" / "iv" / "01000001"
    raw.mkdir(parents=True)
    (raw / "2024.complete.json").write_text('{"n_rows": 0}', encoding="utf-8")
    monkeypatch.setattr("usgs_nwis.training_audit.fetch_iv_batch", _no_network)
    result = download_training(cfg, years=[2024])
    assert result["cached"] == 1
    assert result["requests"] == 0
    assert result["site_years_completed"] == 0


def test_disk_guard_stops_cleanly_and_records_skipped(tmp_path: Path, monkeypatch) -> None:
    cfg = _temp_config(tmp_path)
    monkeypatch.setattr("usgs_nwis.training_audit.fetch_iv_batch", _no_network)
    result = download_training(cfg, years=[2024], min_free_disk_gb=1e9)
    assert result["skipped"] == 1
    assert result["requests"] == 0
    manifest = pd.read_csv(
        tmp_path / "experiments" / "training_audit" / "results" / "download_manifest.csv",
        dtype=str,
    )
    assert manifest.loc[0, "status"] == "skipped"
    assert "disk guard" in manifest.loc[0, "message"]


def test_national_report_writes_required_outputs(tmp_path: Path) -> None:
    from usgs_nwis.national_report import build_report

    cfg = _temp_config(tmp_path, sites=("01000001", "01000002", "01000003"))
    catalog_path = tmp_path / "data" / "interim" / "training_audit" / "catalog.parquet"
    catalog = pd.DataFrame(
        {
            "site_no": ["01000001", "01000002", "01000003"],
            "station_name": ["ALPHA CREEK", "TIDAL RIVER", "GAMMA RUN"],
            "state": ["ME", "ME", "ME"],
            "huc8": ["01010001", "01010001", "01010002"],
            "lat": [45.0, 44.0, 43.0],
            "lon": [-69.0, -68.0, -67.0],
            "drainage_area_km2": [100.0, 200.0, None],
            "catalog_q": [True, True, True],
            "catalog_sc": [True, True, True],
            "catalog_paired": [True, True, True],
            "window_overlap": [True, True, True],
            "NDAMS_2009": [0, 2, 0],
            "DEVNLCD06": [1.0, 30.0, 5.0],
            "human_name_flag": [False, True, False],
        }
    )
    catalog.to_parquet(catalog_path, index=False)
    hourly_dir = tmp_path / "data" / "processed" / "training_audit" / "hourly"
    hourly_dir.mkdir(parents=True)
    aggregate_site_hourly(_raw_rows(), cfg).to_parquet(hourly_dir / "01000001.parquet", index=False)
    summary = build_report(cfg)
    results = tmp_path / "experiments" / "training_audit" / "results"
    for name in (
        "station_availability_national.csv",
        "station_availability_national.parquet",
        "core_stations.csv",
        "extended_stations.csv",
        "screening_funnel.csv",
        "NATIONAL_SCREENING_REPORT.md",
    ):
        assert (results / name).exists(), name
    assert summary["candidate_pool_window_overlap"] == 3
    availability = pd.read_csv(
        results / "station_availability_national.csv", dtype={"site_no": str}
    ).set_index("site_no")
    assert bool(availability.loc["01000002", "tidal_ocean_excluded"])
    assert bool(availability.loc["01000002", "hard_exclusion"])
    assert availability.loc["01000003", "download_status"] == "not_attempted"
    assert availability.loc["01000001", "paired_valid_hours"] > 0
    funnel = pd.read_csv(results / "screening_funnel.csv")
    assert funnel["step"].tolist()[-1] == "core_eligible"


def test_station_metrics_handles_station_without_any_valid_data() -> None:
    empty = pd.DataFrame(
        columns=[
            "site_no",
            "param_cd",
            "series_key",
            "series_name",
            "method",
            "time",
            "value",
            "qualifiers",
        ]
    )
    hourly = aggregate_site_hourly(empty, _cfg())
    metrics = _station_metrics(hourly, _cfg())
    assert metrics["paired_valid_hours"] == 0
    assert metrics["core_eligible"] is False
    assert metrics["all_low_state_paired_fraction"] == 0.0
    assert metrics["all_rising_phase_paired_fraction"] == 0.0
