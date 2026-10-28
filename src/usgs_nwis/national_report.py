"""Nationwide availability table, tier lists, screening funnel and report.

The screening protocol is defined in ``docs/SCREENING_PROTOCOL.md`` and the
``screening`` block of ``configs/training_audit.yaml``.  This module only
assembles already-computed station metrics; it never re-filters the frozen
``window_overlap`` candidate pool.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .io import ensure_dir, git_revision, read_parquet, utc_now, write_json, write_parquet
from .training_audit import (
    _audit,
    _config_hash,
    _legacy_membership,
    _manifest,
    _site,
    _station_metrics,
)

_DOWNLOAD_STATUSES = ("success", "cached", "no_data", "failed", "skipped")

_DEFAULT_METRIC_COLUMNS: dict[str, Any] = {
    "q_valid_hours": 0,
    "sc_valid_hours": 0,
    "paired_valid_hours": 0,
    "paired_valid_hours_sensitivity": 0,
    "paired_coverage": 0.0,
    "paired_coverage_sensitivity": 0.0,
    "valid_years": 0,
    "longest_continuous_paired_hours": 0,
    "longest_missing_paired_hours": 0,
    "all_paired_hours": 0,
    "q_estimated_sample_fraction": 0.0,
    "sc_estimated_sample_fraction": 0.0,
    "sc_anomaly_flag_any": False,
    "sc_anomaly_flags": "",
    "core_eligible": False,
    "extended_eligible": False,
    "core_failed_criteria": "",
    "extended_failed_criteria": "",
}

_TIER_COLUMNS = (
    "site_no",
    "station_name",
    "state",
    "huc8",
    "lat",
    "lon",
    "drainage_area_km2",
    "window_overlap",
    "tidal_ocean_excluded",
    "download_status",
    "site_years_with_data",
    "paired_valid_hours",
    "train_valid_years",
    "validation_valid_years",
    "test_valid_years",
    "train_paired_valid_hours",
    "validation_paired_valid_hours",
    "test_paired_valid_hours",
    "train_q_coverage",
    "validation_q_coverage",
    "test_q_coverage",
    "all_low_state_paired_hours",
    "all_mid_state_paired_hours",
    "all_high_state_paired_hours",
    "all_rising_phase_paired_hours",
    "all_falling_phase_paired_hours",
    "q_estimated_sample_fraction",
    "sc_estimated_sample_fraction",
    "sc_anomaly_flags",
    "basin_flags",
    "core_eligible",
    "extended_eligible",
    "core_failed_criteria",
    "extended_failed_criteria",
)


def _download_status_table(results_dir: Path, catalog_sites: pd.Series) -> pd.DataFrame:
    """Per-station download status derived from the site x year manifest."""
    index = pd.Index(
        pd.Series(catalog_sites).map(_site).drop_duplicates().sort_values(), name="site_no"
    )
    table = pd.DataFrame(index=index)
    for status in _DOWNLOAD_STATUSES:
        table[f"{status}_site_years"] = 0
    table["download_years_with_data"] = ""
    table["download_errors"] = ""

    manifest_path = results_dir / "download_manifest.csv"
    if manifest_path.exists() and manifest_path.stat().st_size:
        manifest = pd.read_csv(manifest_path, dtype={"site_no": str})
        if not manifest.empty:
            manifest["site_no"] = manifest["site_no"].map(_site)
            manifest = manifest.drop_duplicates(["site_no", "year"], keep="last")
            counts = manifest.pivot_table(
                index="site_no", columns="status", values="year", aggfunc="count"
            )
            for status in _DOWNLOAD_STATUSES:
                if status in counts:
                    table[f"{status}_site_years"] = (
                        counts[status].reindex(index, fill_value=0).fillna(0).astype(int)
                    )
            years = (
                manifest[manifest["status"].isin(["success", "cached"])]
                .groupby("site_no")["year"]
                .apply(lambda values: "|".join(str(int(v)) for v in sorted(values.unique())))
            )
            table["download_years_with_data"] = years.reindex(index).fillna("")

    failed_path = results_dir / "failed_requests.csv"
    if failed_path.exists() and failed_path.stat().st_size:
        failed = pd.read_csv(failed_path, dtype={"site_no": str})
        if not failed.empty and "error" in failed:
            failed["site_no"] = failed["site_no"].map(_site)
            errors = failed.groupby("site_no")["error"].apply(
                lambda values: "; ".join(sorted({str(v)[:180] for v in values.dropna()}))
            )
            table["download_errors"] = errors.reindex(index).fillna("")

    def classify(row: pd.Series) -> str:
        failed = int(row["failed_site_years"])
        good = int(row["success_site_years"] + row["cached_site_years"])
        empty = int(row["no_data_site_years"])
        skipped = int(row["skipped_site_years"])
        if failed and (good or empty):
            return "partial_failed"
        if failed:
            return "failed"
        if good:
            return "downloaded"
        if empty:
            return "no_data"
        if skipped:
            return "skipped"
        return "not_attempted"

    table["download_status"] = table.apply(classify, axis=1)
    table["site_years_with_data"] = (
        table["success_site_years"] + table["cached_site_years"] + table["no_data_site_years"]
    )
    return table.reset_index()


def _basin_conditions(frame: pd.DataFrame) -> pd.DataFrame:
    """Hard exclusion is tidal/ocean influence only; everything else is a flag."""
    frame = frame.copy()
    name = frame["station_name"].fillna("").astype(str).str.upper()
    pattern = (
        r"\b(?:TIDAL|TIDALLY|ESTUARY|ESTUARINE|BAY|SOUND|INLET|OCEAN|GULF|BRACKISH|SEAWATER)\b"
    )
    frame["tidal_ocean_excluded"] = name.str.contains(pattern, regex=True)
    frame["tidal_ocean_exclusion_rule"] = (
        "station-name keyword rule; no estuary polygon test was applied in this stage"
    )
    pool = frame["window_overlap"].fillna(False).astype(bool)
    frame["basin_boundary_missing"] = True
    frame["area_attribute_missing"] = frame["drainage_area_km2"].isna()
    frame["reservoir_regulation_flag"] = (
        pd.to_numeric(frame.get("NDAMS_2009"), errors="coerce").fillna(0).ge(1)
    )
    frame["urbanization_flag"] = (
        pd.to_numeric(frame.get("DEVNLCD06"), errors="coerce").fillna(0).ge(20)
    )
    frame["human_name_flag"] = frame.get("human_name_flag", False)
    frame["human_name_flag"] = frame["human_name_flag"].fillna(False).astype(bool)
    frame["nesting_same_huc8_flag"] = frame["huc8"].duplicated(keep=False) & pool
    frame["point_source_data_unavailable"] = True
    frame["road_salt_data_unavailable"] = True
    flag_columns = [
        "basin_boundary_missing",
        "area_attribute_missing",
        "reservoir_regulation_flag",
        "urbanization_flag",
        "human_name_flag",
        "nesting_same_huc8_flag",
        "point_source_data_unavailable",
        "road_salt_data_unavailable",
    ]
    frame["basin_flags"] = frame[flag_columns].apply(
        lambda row: "|".join(column for column in flag_columns if bool(row[column])), axis=1
    )
    frame["basin_flags_are_exclusionary"] = False
    frame["hard_exclusion"] = frame["tidal_ocean_excluded"]
    frame["hard_exclusion_reason"] = np.where(
        frame["tidal_ocean_excluded"], "tidal_or_ocean_influenced_station_name", ""
    )
    return frame


def _training_tier(row: pd.Series) -> str:
    if bool(row.get("core_eligible", False)):
        return "T3_hourly_core"
    if bool(row.get("extended_eligible", False)):
        return "T2_hourly_extended"
    if float(row.get("paired_valid_hours", 0) or 0) > 0:
        return "T1_hourly_overlap"
    return "T0_catalog_only"


def _station_failure_reasons(row: pd.Series) -> str:
    reasons: list[str] = []
    if not bool(row.get("window_overlap", False)):
        reasons.append("outside_frozen_candidate_pool")
    if bool(row.get("tidal_ocean_excluded", False)):
        reasons.append("tidal_or_ocean_influenced")
    status = str(row.get("download_status", ""))
    if status in {"failed", "partial_failed"}:
        reasons.append(f"download_{status}")
    if status in {"skipped", "not_attempted"} and bool(row.get("window_overlap", False)):
        reasons.append(f"download_{status}")
    for tier in ("extended", "core"):
        text = str(row.get(f"{tier}_failed_criteria", "") or "")
        reasons.extend(f"{tier}:{item}" for item in text.split(";") if item)
    return ";".join(dict.fromkeys(reasons))


def _funnel(availability: pd.DataFrame) -> pd.DataFrame:
    union = pd.Series(True, index=availability.index)
    pool = availability["window_overlap"].fillna(False).astype(bool)
    not_tidal = pool & ~availability["tidal_ocean_excluded"]
    downloaded = not_tidal & availability["site_years_with_data"].gt(0)
    paired_any = downloaded & availability["paired_valid_hours"].gt(0)
    any_valid_year = paired_any & availability[
        ["train_valid_years", "validation_valid_years", "test_valid_years"]
    ].apply(pd.to_numeric, errors="coerce").fillna(0).max(axis=1).gt(0)
    extended = availability["extended_eligible"].fillna(False).astype(bool)
    core = availability["core_eligible"].fillna(False).astype(bool)

    def fixed(reason: str):
        return lambda row: reason

    def download_reason(row: pd.Series) -> str:
        return str(row.get("download_status", "")) or "not_downloaded"

    def criteria(column: str):
        return lambda row: str(row.get(column, "") or "") or "unmet_criteria"

    layers = [
        ("catalog_union", union, None, None, fixed("")),
        (
            "catalog_paired",
            availability["catalog_paired"],
            "catalog_union",
            union,
            fixed("not_catalog_paired"),
        ),
        (
            "window_overlap",
            pool,
            "catalog_paired",
            availability["catalog_paired"],
            fixed("no_catalog_overlap_in_window"),
        ),
        (
            "not_tidal_or_ocean",
            not_tidal,
            "window_overlap",
            pool,
            fixed("tidal_or_ocean_influenced"),
        ),
        ("downloaded", downloaded, "not_tidal_or_ocean", not_tidal, download_reason),
        ("has_paired_hours", paired_any, "downloaded", downloaded, fixed("no_paired_valid_hour")),
        ("has_valid_year", any_valid_year, "has_paired_hours", paired_any, fixed("no_valid_year")),
        (
            "extended_eligible",
            extended,
            "has_valid_year",
            any_valid_year,
            criteria("extended_failed_criteria"),
        ),
        (
            "core_eligible",
            core,
            "extended_eligible",
            extended,
            criteria("core_failed_criteria"),
        ),
    ]
    by_site = availability.drop_duplicates("site_no").set_index("site_no")
    rows = []
    for layer, mask, parent_layer, parent_mask, reason_for in layers:
        mask = mask.fillna(False).astype(bool)
        current = set(availability.loc[mask, "site_no"])
        parent = (
            set(availability.loc[parent_mask.fillna(False).astype(bool), "site_no"])
            if parent_layer
            else set()
        )
        dropped = parent - current
        reason_counts: Counter[str] = Counter()
        for site_no in dropped:
            if site_no not in by_site.index:
                continue
            text = reason_for(by_site.loc[site_no])
            reason_counts.update(item for item in text.split(";") if item)
        rows.append(
            {
                "step": layer,
                "relative_to": parent_layer or "",
                "station_count": len(current),
                "dropped_from_parent": len(dropped),
                "drop_reasons_json": json.dumps(reason_counts.most_common()),
            }
        )
    return pd.DataFrame(rows)


def _station_metric_frame(cfg, hourly_dir: Path) -> pd.DataFrame:
    """Reuse per-station metrics unless the hourly files or config changed."""
    paths = sorted(hourly_dir.glob("*.parquet")) if hourly_dir.exists() else []
    processed = ensure_dir(cfg.data_dir / "processed" / "training_audit")
    cache_path = processed / "station_metrics.parquet"
    stamp_path = processed / "station_metrics.json"
    stamp = {
        "config_sha256": _config_hash(cfg),
        "hourly_files": len(paths),
        "newest_mtime_ns": max((path.stat().st_mtime_ns for path in paths), default=0),
    }
    if cache_path.exists() and stamp_path.exists():
        try:
            if json.loads(stamp_path.read_text(encoding="utf-8")) == stamp:
                cached = read_parquet(cache_path)
                cached["site_no"] = cached["site_no"].map(_site)
                return cached
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    metrics = []
    for path in paths:
        record = {"site_no": _site(path.stem)}
        record.update(_station_metrics(read_parquet(path), cfg))
        metrics.append(record)
    frame = pd.DataFrame(metrics)
    if frame.empty:
        return pd.DataFrame({"site_no": pd.Series(dtype=str)})
    frame["site_no"] = frame["site_no"].map(_site)
    write_parquet(frame, cache_path)
    stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    return frame


def build_report(cfg) -> dict[str, Any]:
    """Build the nationwide availability table, tier lists, funnel and report."""
    catalog = read_parquet(cfg.data_dir / "interim" / "training_audit" / "catalog.parquet")
    catalog["site_no"] = catalog["site_no"].map(_site)

    hourly_dir = cfg.data_dir / "processed" / "training_audit" / "hourly"
    metric_frame = _station_metric_frame(cfg, hourly_dir)

    availability = catalog.merge(metric_frame, on="site_no", how="left")
    for column, default in _DEFAULT_METRIC_COLUMNS.items():
        if column not in availability:
            availability[column] = default
        availability[column] = availability[column].fillna(default)
    for column in availability.columns:
        if column.endswith("_eligible") or column == "sc_anomaly_flag_any":
            availability[column] = availability[column].fillna(False).astype(bool)

    results = ensure_dir(cfg.results_dir)
    download = _download_status_table(results, catalog["site_no"])
    availability = availability.merge(download, on="site_no", how="left")
    pool = availability["window_overlap"].fillna(False).astype(bool)
    availability["download_status"] = availability["download_status"].fillna(
        pd.Series(
            np.where(pool, "not_attempted", "not_in_candidate_pool"),
            index=availability.index,
        )
    )
    for column in availability.columns:
        if column.endswith("_site_years") or column == "site_years_with_data":
            availability[column] = availability[column].fillna(0).astype(int)
    for column in ("download_years_with_data", "download_errors"):
        availability[column] = availability[column].fillna("")

    availability = _basin_conditions(availability)
    legacy114, diagnostic = _legacy_membership(cfg.root)
    availability["legacy_114_member"] = availability["site_no"].isin(legacy114)
    availability["legacy_diagnostic_member"] = availability["site_no"].isin(diagnostic)
    availability["training_tier"] = availability.apply(_training_tier, axis=1)
    availability["screening_failed_reasons"] = availability.apply(_station_failure_reasons, axis=1)
    availability["hourly_file_exists"] = availability["site_no"].isin(set(metric_frame["site_no"]))
    availability["yearly_q_sc_paired_hours_note"] = (
        "yearly *_coverage_json holds hours and coverage per calendar year"
    )

    core = availability[availability["core_eligible"]].copy()
    extended = availability[availability["extended_eligible"]].copy()
    tier_columns = [column for column in _TIER_COLUMNS if column in availability]
    tier_columns += [
        column
        for column in availability.columns
        if column.startswith("all_") and column.endswith("_paired_hours")
    ]
    tier_columns = list(dict.fromkeys(tier_columns))

    availability.to_csv(results / "station_availability_national.csv", index=False)
    write_parquet(availability, results / "station_availability_national.parquet")
    core[tier_columns].to_csv(results / "core_stations.csv", index=False)
    extended[tier_columns].to_csv(results / "extended_stations.csv", index=False)
    manifest = _manifest(hourly_dir, cfg) if hourly_dir.exists() else pd.DataFrame()
    manifest.to_csv(results / "hourly_files_manifest.csv", index=False)

    funnel = _funnel(availability)
    funnel.to_csv(results / "screening_funnel.csv", index=False)

    rules = _audit(cfg)
    download_execution_path = results / "download_summary.json"
    download_execution = (
        json.loads(download_execution_path.read_text(encoding="utf-8"))
        if download_execution_path.exists()
        else {}
    )
    manifest_frame = (
        pd.read_csv(results / "download_manifest.csv", dtype={"site_no": str})
        if (results / "download_manifest.csv").exists()
        else pd.DataFrame()
    )
    download_counts = (
        manifest_frame["status"].value_counts().to_dict() if not manifest_frame.empty else {}
    )
    summary = {
        "generated_at": utc_now(),
        "config_sha256": _config_hash(cfg),
        "git": git_revision(cfg.root),
        "scope": "CONUS states configured; 2015-2024; IV Q=00060 and SC=00095",
        "stations_in_catalog": int(len(availability)),
        "candidate_pool_window_overlap": int(availability["window_overlap"].sum()),
        "core_stations": int(availability["core_eligible"].sum()),
        "extended_stations": int(availability["extended_eligible"].sum()),
        "download_status_counts": availability["download_status"].value_counts().to_dict(),
        "site_year_download_counts": download_counts,
        "funnel": dict(zip(funnel["step"], funnel["station_count"], strict=True)),
        "screening_rules": {
            "splits": rules["splits"],
            "valid_day_min_paired_hours": rules["valid_day_min_paired_hours"],
            "valid_year_min_valid_days": rules["valid_year_min_valid_days"],
            "core": rules["tiers"]["core"],
            "extended": rules["tiers"]["extended"],
            "flow_state_quantiles": rules["flow_state_quantiles"],
        },
        "local_download_execution": download_execution,
        "limitations": [
            "no basin boundary polygons were used; boundary and nesting flags are HUC8 proxies",
            "no point-source or road-salt datasets were joined; both are reported as unavailable",
            "tidal/ocean exclusion uses station-name keywords only",
            "raw IV Parquet and national hourly Parquet are not committed",
        ],
    }
    write_json(results / "audit_summary.json", summary)
    _write_national_report(results / "NATIONAL_SCREENING_REPORT.md", summary, funnel, availability)
    protocol = cfg.root / "docs" / "SCREENING_PROTOCOL.md"
    if protocol.exists():
        (results / "SCREENING_PROTOCOL.md").write_text(
            protocol.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return summary


def _write_national_report(
    path: Path, summary: dict, funnel: pd.DataFrame, availability: pd.DataFrame
) -> None:
    def markdown(frame: pd.DataFrame) -> str:
        columns = [str(column) for column in frame.columns]
        rows = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join(["---"] * len(columns)) + " |",
        ]
        rows.extend(
            "| " + " | ".join(str(value) for value in row) + " |"
            for row in frame.itertuples(index=False, name=None)
        )
        return "\n".join(rows)

    tier_frame = (
        availability["training_tier"]
        .value_counts()
        .rename_axis("tier")
        .reset_index(name="stations")
    )
    status_frame = (
        availability["download_status"]
        .value_counts()
        .rename_axis("download_status")
        .reset_index(name="stations")
    )
    anomaly_counts = {
        name: int(availability["sc_anomaly_flags"].fillna("").str.contains(name, regex=False).sum())
        for name in (
            "sc_constant_gt_30d",
            "sc_isolated_spike",
            "sc_interannual_median_shift",
            "sc_long_term_monotonic_change",
            "sc_instrument_or_range_switch",
        )
    }
    download = summary.get("site_year_download_counts", {})
    execution = summary.get("local_download_execution", {})
    limitations = "\n".join(f"- {item}" for item in summary["limitations"])
    text = f"""# National Q–SC screening report

Generated: {summary["generated_at"]}
Config SHA256: `{summary["config_sha256"]}`
Commit: `{summary.get("git", {}).get("commit", "unknown")}`

## Scope

{summary["scope"]}. Records come from the legacy instantaneous-values service,
`00060` (discharge, ft3/s) and `00095` (specific conductance, uS/cm at 25 C),
converted to UTC and aggregated to hourly means without interpolation. The
frozen candidate pool is the catalog `window_overlap == true` layer, which
contains {summary["candidate_pool_window_overlap"]} stations and was not
re-filtered in this stage.

## Screening funnel

{markdown(funnel)}

## Download status

{markdown(status_frame)}

Site-year outcomes recorded in `download_manifest.csv`:

```json
{json.dumps(download, indent=2, sort_keys=True)}
```

Last download run: requests={execution.get("requests", "n/a")},
rows={execution.get("rows", "n/a")}, years={execution.get("years", "n/a")},
sites selected={execution.get("sites_selected", "n/a")}.

## Training tiers

{markdown(tier_frame)}

## Core and extended sets

- Core, used for the main conclusions: {summary["core_stations"]} stations.
- Extended, for larger joint training and robustness: {summary["extended_stations"]} stations.
- Every threshold is the one in `configs/training_audit.yaml`; no threshold was
  changed after the national run.

## SC anomaly flags (diagnostic only)

```json
{json.dumps(anomaly_counts, indent=2)}
```

An anomaly flag never removes a station or a value. Only missing or
non-numeric values, `SC <= 0`, provisional (`P`) or explicitly invalid
qualifiers, and unresolvable duplicate timestamps are marked invalid.

## Read together with

- `SCREENING_PROTOCOL.md`: fixed rules and reproduction commands.
- `station_availability_national.csv`: one row per catalog station with
  download status, yearly Q/SC/paired hours, split statistics, Q coverage,
  flow-state and phase coverage, anomaly flags and every failed criterion.
- `core_stations.csv`, `extended_stations.csv`, `screening_funnel.csv`.
- `download_manifest.csv`, `failed_requests.csv`.

## Limitations

{limitations}
"""
    path.write_text(text, encoding="utf-8")
