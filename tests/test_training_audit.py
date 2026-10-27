from __future__ import annotations

import gzip
import re
from pathlib import Path

import pandas as pd

from usgs_nwis.config import load_config
from usgs_nwis.training_audit import _runs, aggregate_site_hourly, parse_training_catalog

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


def test_primary_mask_uses_approved_and_sensitivity_keeps_provisional() -> None:
    frame = _raw_rows()
    frame.loc[(frame["param_cd"] == "00095") & (frame.index == 7), "qualifiers"] = "P"
    hourly = aggregate_site_hourly(frame, _cfg())
    first = hourly.loc[hourly["time"] == pd.Timestamp("2020-01-01T00:00:00Z")].iloc[0]
    assert first["sc_approved_samples"] == 2
    assert first["sc_sensitivity_samples"] == 4
    assert not first["sc_valid"]
    assert first["sc_valid_sensitivity"]
    assert not first["paired_valid"]
    assert first["paired_valid_sensitivity"]


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
