"""Reproducible training-data audit for paired hourly USGS Q and SC.

The audit deliberately keeps catalog eligibility, actual observations, hourly
quality, forcing readiness and diagnostic suitability as separate layers.
No interpolation or forward filling is performed here.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from .api import ApiError, fetch_iv_batch
from .catalog import fetch_catalogs
from .gages import prepare_gages2
from .io import (
    append_jsonl,
    ensure_dir,
    git_revision,
    read_parquet,
    runtime_info,
    sha256_file,
    utc_now,
    write_json,
    write_parquet,
)
from .rdb import parse_rdb


def _audit(cfg) -> dict[str, Any]:
    if not cfg.audit:
        raise ValueError("configuration has no `audit` section")
    return cfg.audit


def _site(value: Any) -> str:
    text = "" if pd.isna(value) else str(value).strip()
    return text.zfill(8) if text.isdigit() and len(text) < 8 else text


def _config_hash(cfg) -> str:
    payload = cfg.path.read_bytes()
    return hashlib.sha256(payload).hexdigest()


def _catalog_rows(cfg) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    raw_dir = cfg.data_dir / "raw" / "catalog"
    wanted = set(cfg.params.values())
    keep = [
        "site_no",
        "station_nm",
        "dec_lat_va",
        "dec_long_va",
        "huc_cd",
        "parm_cd",
        "ts_id",
        "begin_date",
        "end_date",
        "count_nu",
    ]
    for state in cfg.data.states:
        path = raw_dir / f"{state}.rdb.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rb") as handle:
            frame = parse_rdb(handle.read().decode("latin-1"))
        if frame.empty or not set(keep).issubset(frame.columns):
            continue
        frame = frame[(frame["data_type_cd"] == "uv") & frame["parm_cd"].isin(wanted)].copy()
        if frame.empty:
            continue
        frame["state"] = state
        frames.append(frame[keep + ["state"]])
    if not frames:
        raise RuntimeError("no cached catalog rows; run training-audit catalog --refresh")
    rows = pd.concat(frames, ignore_index=True)
    rows["site_no"] = rows["site_no"].map(_site)
    rows["begin_date"] = pd.to_datetime(rows["begin_date"], errors="coerce")
    rows["end_date"] = pd.to_datetime(rows["end_date"], errors="coerce")
    rows["count_nu"] = pd.to_numeric(rows["count_nu"], errors="coerce").fillna(0)
    rows["dec_lat_va"] = pd.to_numeric(rows["dec_lat_va"], errors="coerce")
    rows["dec_long_va"] = pd.to_numeric(rows["dec_long_va"], errors="coerce")
    return rows


def parse_training_catalog(cfg) -> pd.DataFrame:
    """Build the union Q/SC population without legacy candidate filters."""
    rows = _catalog_rows(cfg)
    rows = rows.sort_values(
        ["site_no", "parm_cd", "count_nu", "end_date"],
        ascending=[True, True, False, False],
    ).drop_duplicates(["site_no", "parm_cd"], keep="first")
    q_code = cfg.params["streamflow"]
    sc_code = cfg.params["specific_conductance"]
    metadata = (
        rows.sort_values(["site_no", "count_nu"], ascending=[True, False])
        .drop_duplicates("site_no")
        .set_index("site_no")
    )
    indexed = rows.set_index(["site_no", "parm_cd"])
    sites = sorted(rows["site_no"].unique())
    out = pd.DataFrame(index=pd.Index(sites, name="site_no"))
    out["station_name"] = metadata["station_nm"]
    out["state"] = metadata["state"]
    out["lat"] = metadata["dec_lat_va"]
    out["lon"] = metadata["dec_long_va"]
    huc = metadata["huc_cd"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    out["huc2"] = huc.str.zfill(8).str[:2]
    out["huc8"] = huc.str.zfill(8).str[:8]
    for code, suffix in ((q_code, "q"), (sc_code, "sc")):
        part = (
            indexed.xs(code, level="parm_cd")
            if code in indexed.index.get_level_values(1)
            else pd.DataFrame()
        )
        out[f"catalog_{suffix}"] = out.index.isin(part.index)
        for source, target in (
            ("begin_date", f"catalog_{suffix}_start"),
            ("end_date", f"catalog_{suffix}_end"),
            ("count_nu", f"catalog_{suffix}_records"),
            ("ts_id", f"catalog_{suffix}_ts_id"),
        ):
            out[target] = part[source].reindex(out.index) if not part.empty else pd.NA
    out["catalog_paired"] = out["catalog_q"] & out["catalog_sc"]
    out["catalog_overlap_start"] = out[["catalog_q_start", "catalog_sc_start"]].max(axis=1)
    out["catalog_overlap_end"] = out[["catalog_q_end", "catalog_sc_end"]].min(axis=1)
    window_start = pd.Timestamp(cfg.window.start).tz_localize(None)
    window_end = pd.Timestamp(cfg.window.end).tz_localize(None)
    clipped_start = out["catalog_overlap_start"].clip(lower=window_start)
    clipped_end = out["catalog_overlap_end"].clip(upper=window_end)
    overlap = (clipped_end - clipped_start).dt.total_seconds().clip(lower=0)
    out["catalog_overlap_days_in_window"] = overlap / 86400
    out["window_overlap"] = out["catalog_paired"] & (overlap > 0)
    out = out.reset_index()
    return out


def _join_attributes(cfg, sites: pd.DataFrame) -> pd.DataFrame:
    cached = cfg.data_dir / "interim" / "gages2_attributes.parquet"
    try:
        gages = read_parquet(cached) if cached.exists() else prepare_gages2(cfg)
    except (OSError, httpx.HTTPError, ValueError):
        gages = pd.DataFrame(columns=["site_no"])
    if not gages.empty:
        keep = [
            column
            for column in (
                "site_no",
                "DRAIN_SQKM",
                "CLASS",
                "NDAMS_2009",
                "DDENS_2009",
                "MAJ_NDAMS_2009",
                "DEVNLCD06",
                "HYDRO_DISTURB_INDX",
            )
            if column in gages.columns
        ]
        gages = gages[keep].copy()
        gages["site_no"] = gages["site_no"].map(_site)
        sites = sites.merge(gages, on="site_no", how="left")
    for column in (
        "DRAIN_SQKM",
        "CLASS",
        "NDAMS_2009",
        "DDENS_2009",
        "MAJ_NDAMS_2009",
        "DEVNLCD06",
        "HYDRO_DISTURB_INDX",
    ):
        if column not in sites:
            sites[column] = pd.NA
    sites["drainage_area_km2"] = pd.to_numeric(sites["DRAIN_SQKM"], errors="coerce")
    sites["gages2_matched"] = sites["drainage_area_km2"].notna()
    sites["basin_boundary_available"] = False
    sites["basin_boundary_source"] = "not_assessed; GAGES-II attributes are not a boundary file"
    sites["human_name_flag"] = (
        sites["station_name"]
        .fillna("")
        .str.contains(
            r"DAM|RESERVOIR|CANAL|DIVERSION|POWER|WWTP|SEWAGE|TREATMENT|IRRIGATION",
            case=False,
            regex=True,
        )
    )
    sites["human_name_flag_rule"] = "name keyword flag only; not proof of impact"
    bounds = _audit(cfg)["conus_bounds"]
    within = sites["lat"].between(bounds["lat_min"], bounds["lat_max"]) & sites["lon"].between(
        bounds["lon_min"], bounds["lon_max"]
    )
    sites["nldas2_geographic_coverage"] = within
    sites["aorc_geographic_coverage"] = within
    sites["forcing_extraction_method"] = "unprepared; product coverage is not downloaded data"
    forcing_root = cfg.data_dir / "raw" / "forcing"
    sites["forcing_file_exists_in_repository"] = sites["site_no"].map(
        lambda site_no: (
            any(forcing_root.rglob(f"*{site_no}*.parquet")) if forcing_root.exists() else False
        )
    )
    sites["forcing_ready"] = sites["drainage_area_km2"].notna() & within
    sites["forcing_missing_reason"] = np.where(
        ~within,
        "outside_configured_CONUS_bounds_or_missing_coordinates",
        np.where(~sites["gages2_matched"], "reliable_area_or_attributes_missing", ""),
    )
    return sites


def build_training_catalog(cfg, *, refresh: bool = False) -> pd.DataFrame:
    asyncio.run(fetch_catalogs(cfg, refresh=refresh))
    sites = _join_attributes(cfg, parse_training_catalog(cfg))
    interim = ensure_dir(cfg.data_dir / "interim" / "training_audit")
    results = ensure_dir(cfg.results_dir)
    write_parquet(sites, interim / "catalog.parquet")
    sites.to_csv(results / "catalog_population.csv", index=False)
    provenance_rows = []
    params = {
        "format": "rdb",
        "siteType": "ST",
        "siteStatus": "all",
        "seriesCatalogOutput": "true",
        "parameterCd": ",".join(sorted(cfg.params.values())),
        "hasDataTypeCd": "iv",
    }
    for state in cfg.data.states:
        path = cfg.data_dir / "raw" / "catalog" / f"{state}.rdb.gz"
        if path.exists():
            provenance_rows.append(
                {
                    "state": state,
                    "service_url": cfg.data.site_service,
                    "query_parameters_json": json.dumps({**params, "stateCd": state}),
                    "cache_relative_path": path.relative_to(cfg.root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "cache_mtime_utc": pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC"),
                }
            )
    pd.DataFrame(provenance_rows).to_csv(results / "catalog_provenance.csv", index=False)
    write_json(
        results / "run_environment.json",
        {
            "generated_at": utc_now(),
            "runtime": runtime_info(),
            "git": git_revision(cfg.root),
            "config_sha256": _config_hash(cfg),
        },
    )
    return sites


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


async def _download_training_async(
    cfg, *, refresh: bool = False, site_limit: int | None = None, years: list[int] | None = None
) -> dict[str, Any]:
    catalog = read_parquet(cfg.data_dir / "interim" / "training_audit" / "catalog.parquet")
    sites = catalog[catalog["window_overlap"]].sort_values("site_no")["site_no"].map(_site).tolist()
    if site_limit is not None:
        sites = sites[:site_limit]
    years = years or list(cfg.window.years)
    raw_root = ensure_dir(cfg.data_dir / "raw" / "training_audit" / "iv")
    provenance = ensure_dir(cfg.results_dir) / "data_provenance.jsonl"
    errors = cfg.results_dir / "errors.jsonl"
    progress = {"requests": 0, "rows": 0, "site_years_completed": 0, "cached": 0}
    limits = httpx.Limits(max_connections=max(2, cfg.data.concurrency))
    async with httpx.AsyncClient(
        timeout=cfg.data.request_timeout_s,
        limits=limits,
        headers={"User-Agent": "usgs-nwis-training-audit/0.1"},
    ) as client:
        for year in years:
            pending = []
            for site_no in sites:
                marker = raw_root / site_no / f"{year}.complete.json"
                if marker.exists() and not refresh:
                    progress["cached"] += 1
                else:
                    pending.append(site_no)
            for batch in _chunks(pending, cfg.data.batch_sites):
                try:
                    result = await fetch_iv_batch(
                        client,
                        cfg.data.iv_service,
                        batch,
                        list(cfg.params.values()),
                        f"{year}-01-01",
                        f"{year}-12-31",
                        retries=cfg.data.retries,
                    )
                except ApiError as exc:
                    append_jsonl(
                        errors,
                        {
                            "stage": "training_audit_download",
                            "year": year,
                            "sites": batch,
                            "error": str(exc),
                            "at": utc_now(),
                        },
                    )
                    continue
                append_jsonl(
                    provenance,
                    {
                        "stage": "training_audit_download",
                        "year": year,
                        "sites": batch,
                        "url": result.url,
                        "retrieved_at": result.retrieved_at,
                        "sha256": result.sha256,
                        "n_rows": result.n_rows,
                        "missing_sites": list(result.missing_sites),
                    },
                )
                for site_no in batch:
                    directory = ensure_dir(raw_root / site_no)
                    part = result.frame[result.frame["site_no"].map(_site) == site_no].copy()
                    if not part.empty:
                        write_parquet(part, directory / f"{year}.parquet")
                    write_json(
                        directory / f"{year}.complete.json",
                        {
                            "site_no": site_no,
                            "year": year,
                            "n_rows": len(part),
                            "request_sha256": result.sha256,
                            "completed_at": utc_now(),
                        },
                    )
                    progress["site_years_completed"] += 1
                progress["requests"] += 1
                progress["rows"] += result.n_rows
    progress.update({"sites_selected": len(sites), "years": years, "finished_at": utc_now()})
    write_json(cfg.results_dir / "download_summary.json", progress)
    return progress


def download_training(
    cfg, *, refresh: bool = False, site_limit: int | None = None, years: list[int] | None = None
) -> dict[str, Any]:
    return asyncio.run(
        _download_training_async(cfg, refresh=refresh, site_limit=site_limit, years=years)
    )


def _choose_series(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame.dropna(subset=["value"])
    if valid.empty:
        return valid
    counts = (
        valid.groupby(["param_cd", "series_key"], dropna=False)
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["param_cd", "n", "series_key"], ascending=[True, False, True])
        .drop_duplicates("param_cd")
    )
    return frame.merge(counts[["param_cd", "series_key"]], on=["param_cd", "series_key"])


def _cadence_by_year(part: pd.DataFrame) -> dict[int, float]:
    result: dict[int, float] = {}
    clean = part.dropna(subset=["time", "value"]).sort_values("time").drop_duplicates("time")
    for year, group in clean.groupby(clean["time"].dt.year):
        diffs = group["time"].diff().dt.total_seconds().div(60)
        diffs = diffs[(diffs > 0) & (diffs <= 360)]
        if not diffs.empty:
            rounded = diffs.round(3)
            result[int(year)] = float(rounded.mode().iloc[0])
    return result


def _qualifier_class(text: Any) -> str:
    value = "" if pd.isna(text) else str(text).upper()
    if "A" in value:
        return "approved"
    if "P" in value:
        return "provisional"
    return "other"


def _hourly_param(part: pd.DataFrame, prefix: str, cfg) -> tuple[pd.DataFrame, dict[int, float]]:
    rules = _audit(cfg)
    cadence = _cadence_by_year(part)
    value_range = rules["q_valid_range"] if prefix == "q" else rules["sc_valid_range_us_cm"]
    part = part.copy()
    part["qual_class"] = part["qualifiers"].map(_qualifier_class)
    part["range_ok"] = part["value"].between(value_range[0], value_range[1], inclusive="both")
    part["approved_ok"] = part["range_ok"] & part["qual_class"].eq("approved")
    accepted = {str(x).upper() for x in rules["accepted_nonapproved_qualifiers"]}
    part["sensitivity_ok"] = part["range_ok"] & (
        part["qual_class"].eq("approved")
        | part["qualifiers"].fillna("").str.upper().map(lambda x: any(q in x for q in accepted))
    )
    part["hour"] = part["time"].dt.floor("h")

    def summarize(group: pd.DataFrame) -> pd.Series:
        approved = group.loc[group["approved_ok"], "value"]
        sensitivity = group.loc[group["sensitivity_ok"], "value"]
        return pd.Series(
            {
                f"{prefix}_value": approved.mean(),
                f"{prefix}_median": approved.median(),
                f"{prefix}_std": approved.std(ddof=0),
                f"{prefix}_min": approved.min(),
                f"{prefix}_max": approved.max(),
                f"{prefix}_native_samples": len(group),
                f"{prefix}_unique_samples": group["time"].nunique(),
                f"{prefix}_approved_samples": int(group["approved_ok"].sum()),
                f"{prefix}_sensitivity_samples": int(group["sensitivity_ok"].sum()),
                f"{prefix}_value_sensitivity": sensitivity.mean(),
                f"{prefix}_quality_flags": "|".join(
                    sorted(set(group["qualifiers"].fillna("").astype(str)))
                ),
                f"{prefix}_duplicate_timestamps": int(len(group) - group["time"].nunique()),
                f"{prefix}_negative_samples": int((group["value"] < 0).sum()),
                f"{prefix}_range_failure_samples": int(
                    (~group["range_ok"] & group["value"].notna()).sum()
                ),
            }
        )

    grouped = part.groupby("hour", sort=True).apply(summarize, include_groups=False)
    grouped.index.name = "time"
    return grouped, cadence


def aggregate_site_hourly(frame: pd.DataFrame, cfg) -> pd.DataFrame:
    """Aggregate one site's native records to UTC hours without interpolation."""
    required = {"param_cd", "series_key", "time", "value", "qualifiers"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"raw frame missing columns: {sorted(missing)}")
    frame = frame.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = _choose_series(frame.dropna(subset=["time"]))
    q = frame[frame["param_cd"] == cfg.params["streamflow"]]
    sc = frame[frame["param_cd"] == cfg.params["specific_conductance"]]
    qh, qcad = _hourly_param(q, "q", cfg) if not q.empty else (pd.DataFrame(), {})
    sh, scad = _hourly_param(sc, "sc", cfg) if not sc.empty else (pd.DataFrame(), {})
    index = pd.date_range(
        pd.Timestamp(cfg.window.start).floor("h"),
        pd.Timestamp(cfg.window.end).floor("h"),
        freq="h",
        tz="UTC",
    )
    hourly = pd.DataFrame(index=index).join(qh, how="left").join(sh, how="left")
    hourly.index.name = "time"
    for prefix, cadence in (("q", qcad), ("sc", scad)):
        for name in ("value", "median", "std", "min", "max", "value_sensitivity"):
            column = f"{prefix}_{name}"
            if column not in hourly:
                hourly[column] = np.nan
        for name in (
            "native_samples",
            "unique_samples",
            "approved_samples",
            "sensitivity_samples",
            "duplicate_timestamps",
            "negative_samples",
            "range_failure_samples",
        ):
            column = f"{prefix}_{name}"
            if column not in hourly:
                hourly[column] = 0
        quality_column = f"{prefix}_quality_flags"
        if quality_column not in hourly:
            hourly[quality_column] = ""
        expected = pd.Series(hourly.index.year, index=hourly.index).map(
            {year: max(1.0, round(60.0 / value)) for year, value in cadence.items() if value > 0}
        )
        hourly[f"{prefix}_expected_samples"] = expected
        hourly[f"{prefix}_hour_coverage"] = (
            hourly.get(f"{prefix}_approved_samples", 0) / expected
        ).clip(upper=1)
        hourly[f"{prefix}_sensitivity_coverage"] = (
            hourly.get(f"{prefix}_sensitivity_samples", 0) / expected
        ).clip(upper=1)
        hourly[f"{prefix}_valid"] = (
            hourly[f"{prefix}_hour_coverage"]
            .fillna(0)
            .ge(float(_audit(cfg)["hourly_min_coverage"]))
        )
        hourly[f"{prefix}_valid_sensitivity"] = (
            hourly[f"{prefix}_sensitivity_coverage"]
            .fillna(0)
            .ge(float(_audit(cfg)["hourly_min_coverage"]))
        )
        hourly[f"{prefix}_native_cadence_minutes"] = pd.Series(
            hourly.index.year, index=hourly.index
        ).map(cadence)
    hourly["paired_valid"] = hourly["q_valid"] & hourly["sc_valid"]
    hourly["paired_valid_sensitivity"] = (
        hourly["q_valid_sensitivity"] & hourly["sc_valid_sensitivity"]
    )
    hourly["missing_or_failure_reason"] = np.select(
        [
            ~hourly["q_valid"] & ~hourly["sc_valid"],
            ~hourly["q_valid"],
            ~hourly["sc_valid"],
        ],
        ["q_and_sc_missing_or_quality", "q_missing_or_quality", "sc_missing_or_quality"],
        default="",
    )
    hourly["was_aggregated"] = hourly["q_native_samples"].fillna(0).gt(1) | hourly[
        "sc_native_samples"
    ].fillna(0).gt(1)
    hourly["was_interpolated"] = False
    hourly["was_forward_filled"] = False
    hourly["processing"] = "UTC hour mean of valid native observations; no cross-hour fill"
    return hourly.reset_index()


def build_hourly(cfg, *, site_limit: int | None = None, refresh: bool = False) -> dict[str, Any]:
    raw_root = cfg.data_dir / "raw" / "training_audit" / "iv"
    out_root = ensure_dir(cfg.data_dir / "processed" / "training_audit" / "hourly")
    sites = (
        sorted(path.name for path in raw_root.iterdir() if path.is_dir())
        if raw_root.exists()
        else []
    )
    if site_limit is not None:
        sites = sites[:site_limit]
    written = 0
    skipped = 0
    for site_no in sites:
        destination = out_root / f"{site_no}.parquet"
        if destination.exists() and not refresh:
            skipped += 1
            continue
        files = sorted((raw_root / site_no).glob("*.parquet"))
        if not files:
            continue
        raw = pd.concat([read_parquet(path) for path in files], ignore_index=True)
        hourly = aggregate_site_hourly(raw, cfg)
        write_parquet(hourly, destination)
        written += 1
    result = {"sites_written": written, "sites_skipped": skipped, "finished_at": utc_now()}
    write_json(cfg.results_dir / "hourly_summary.json", result)
    return result


def _runs(mask: pd.Series) -> list[int]:
    values = mask.fillna(False).astype(bool).to_numpy()
    if not len(values):
        return []
    change = np.r_[True, values[1:] != values[:-1], True]
    boundaries = np.flatnonzero(change)
    return [int(b - a) for a, b in zip(boundaries[:-1], boundaries[1:], strict=True) if values[a]]


def _coverage_records(hourly: pd.DataFrame, column: str) -> tuple[str, str, int]:
    temp = hourly.assign(year=hourly["time"].dt.year, month=hourly["time"].dt.month)
    yearly = temp.groupby("year")[column].agg(["sum", "size"])
    yearly["coverage"] = yearly["sum"] / yearly["size"]
    season_map = {
        12: "DJF",
        1: "DJF",
        2: "DJF",
        3: "MAM",
        4: "MAM",
        5: "MAM",
        6: "JJA",
        7: "JJA",
        8: "JJA",
        9: "SON",
        10: "SON",
        11: "SON",
    }
    seasonal = (
        temp.assign(season=temp["month"].map(season_map))
        .groupby(["year", "season"])[column]
        .agg(["sum", "size"])
    )
    seasonal["coverage"] = seasonal["sum"] / seasonal["size"]
    valid_years = int((yearly["coverage"] >= float(0)).sum())
    return (
        json.dumps(
            {
                str(i): {"hours": int(r["sum"]), "coverage": float(r["coverage"])}
                for i, r in yearly.iterrows()
            }
        ),
        json.dumps(
            {
                f"{i[0]}-{i[1]}": {"hours": int(r["sum"]), "coverage": float(r["coverage"])}
                for i, r in seasonal.iterrows()
            }
        ),
        valid_years,
    )


def _quality_distribution(hourly: pd.DataFrame, prefix: str) -> dict[str, float]:
    flags = hourly[f"{prefix}_quality_flags"].fillna("").astype(str)
    total = int(hourly[f"{prefix}_native_samples"].fillna(0).sum())
    if total == 0:
        return {"approved": 0.0, "provisional": 0.0, "other": 0.0}
    approved = int(hourly[f"{prefix}_approved_samples"].fillna(0).sum())
    provisional_hours = flags.str.upper().str.contains("P", regex=False)
    provisional = int(hourly.loc[provisional_hours, f"{prefix}_native_samples"].fillna(0).sum())
    return {
        "approved": approved / total,
        "provisional": provisional / total,
        "other": max(0.0, 1 - (approved + provisional) / total),
    }


def _station_metrics(hourly: pd.DataFrame, cfg) -> dict[str, Any]:
    hourly = hourly.copy()
    hourly["time"] = pd.to_datetime(hourly["time"], utc=True)
    paired = hourly["paired_valid"].fillna(False)
    qvalid = hourly["q_valid"].fillna(False)
    scvalid = hourly["sc_valid"].fillna(False)
    pair_runs = _runs(paired)
    gap_runs = _runs(~paired)
    rules = _audit(cfg)
    q_year, q_season, _ = _coverage_records(hourly, "q_valid")
    sc_year, sc_season, _ = _coverage_records(hourly, "sc_valid")
    pair_year, pair_season, _ = _coverage_records(hourly, "paired_valid")
    year_table = json.loads(pair_year)
    valid_years = sum(
        item["coverage"] >= float(rules["valid_year_min_paired_coverage"])
        for item in year_table.values()
    )
    sc_values = hourly.loc[scvalid, "sc_value"].dropna()
    diff = sc_values.diff().abs()
    rel = diff / sc_values.shift().abs().clip(lower=1)
    jump = (diff >= float(rules["sc_jump_absolute_us_cm"])) & (
        rel >= float(rules["sc_jump_relative_fraction"])
    )
    q = hourly.loc[qvalid, "q_value"].dropna()
    states = {}
    state_coverage = {}
    if not q.empty:
        quantiles = q.quantile([0.1, 0.33, 0.67, 0.9]).to_dict()
        states = {str(k): float(v) for k, v in quantiles.items()}
        q_all = hourly["q_value"]
        dq = q_all.diff()
        scale = q_all.abs().rolling(24, min_periods=1).median().clip(lower=1e-9)
        phase = np.select(
            [dq > 0.01 * scale, dq < -0.01 * scale],
            ["rising", "falling"],
            default="stable",
        )
        for label in ("rising", "falling", "stable"):
            mask = pd.Series(phase == label, index=hourly.index) & qvalid
            state_coverage[label] = {
                "q_hours": int(mask.sum()),
                "sc_valid_hours": int((mask & scvalid).sum()),
            }
        state_coverage["low_flow"] = int((scvalid & q_all.le(quantiles[0.33])).sum())
        state_coverage["mid_flow"] = int(
            (scvalid & q_all.gt(quantiles[0.33]) & q_all.lt(quantiles[0.67])).sum()
        )
        state_coverage["high_flow"] = int((scvalid & q_all.ge(quantiles[0.67])).sum())
    return {
        "actual_q_start": hourly.loc[qvalid, "time"].min(),
        "actual_q_end": hourly.loc[qvalid, "time"].max(),
        "actual_sc_start": hourly.loc[scvalid, "time"].min(),
        "actual_sc_end": hourly.loc[scvalid, "time"].max(),
        "actual_overlap_start": hourly.loc[paired, "time"].min(),
        "actual_overlap_end": hourly.loc[paired, "time"].max(),
        "q_valid_hours": int(qvalid.sum()),
        "sc_valid_hours": int(scvalid.sum()),
        "paired_valid_hours": int(paired.sum()),
        "paired_valid_hours_sensitivity": int(
            hourly["paired_valid_sensitivity"].fillna(False).sum()
        ),
        "paired_coverage": float(paired.mean()),
        "paired_coverage_sensitivity": float(
            hourly["paired_valid_sensitivity"].fillna(False).mean()
        ),
        "longest_continuous_paired_hours": max(pair_runs, default=0),
        "longest_missing_paired_hours": max(gap_runs, default=0),
        **{
            f"gaps_ge_{int(threshold)}h": sum(run >= threshold for run in gap_runs)
            for threshold in rules["gap_threshold_hours"]
        },
        "valid_years": int(valid_years),
        "q_yearly_coverage_json": q_year,
        "sc_yearly_coverage_json": sc_year,
        "paired_yearly_coverage_json": pair_year,
        "q_seasonal_coverage_json": q_season,
        "sc_seasonal_coverage_json": sc_season,
        "paired_seasonal_coverage_json": pair_season,
        "q_quality_distribution_json": json.dumps(_quality_distribution(hourly, "q")),
        "sc_quality_distribution_json": json.dumps(_quality_distribution(hourly, "sc")),
        "q_duplicate_timestamps": int(hourly["q_duplicate_timestamps"].fillna(0).sum()),
        "sc_duplicate_timestamps": int(hourly["sc_duplicate_timestamps"].fillna(0).sum()),
        "q_negative_fraction": float(
            hourly["q_negative_samples"].fillna(0).sum()
            / max(1, hourly["q_native_samples"].fillna(0).sum())
        ),
        "sc_negative_fraction": float(
            hourly["sc_negative_samples"].fillna(0).sum()
            / max(1, hourly["sc_native_samples"].fillna(0).sum())
        ),
        "q_out_of_range_fraction": float(
            hourly["q_range_failure_samples"].fillna(0).sum()
            / max(1, hourly["q_native_samples"].fillna(0).sum())
        ),
        "sc_out_of_range_fraction": float(
            hourly["sc_range_failure_samples"].fillna(0).sum()
            / max(1, hourly["sc_native_samples"].fillna(0).sum())
        ),
        "sc_jump_fraction": float(jump.mean()) if len(jump) else 0.0,
        "sc_constant_suspect": max(_runs(sc_values.diff().fillna(np.inf).eq(0)), default=0)
        >= int(rules["sc_constant_run_hours"]),
        "hydrologic_state_q_quantiles_json": json.dumps(states),
        "hydrologic_state_sc_coverage_json": json.dumps(state_coverage),
        "months_with_sc": "|".join(
            str(x) for x in sorted(hourly.loc[scvalid, "time"].dt.month.unique())
        ),
        "native_q_cadence_minutes": "|".join(
            str(x) for x in sorted(hourly["q_native_cadence_minutes"].dropna().unique())
        ),
        "native_sc_cadence_minutes": "|".join(
            str(x) for x in sorted(hourly["sc_native_cadence_minutes"].dropna().unique())
        ),
        "sensor_regime_change_flag": (
            hourly["q_native_cadence_minutes"].dropna().nunique() > 1
            or hourly["sc_native_cadence_minutes"].dropna().nunique() > 1
        ),
        "wet_dry_definition": "station Q thirds; climatological drought products not downloaded",
    }


def _legacy_membership(root: Path) -> tuple[set[str], set[str]]:
    census_path = root / "experiments" / "round1" / "results" / "census_station_summary.csv"
    legacy114 = (
        set(pd.read_csv(census_path, dtype={"site_no": str})["site_no"].map(_site))
        if census_path.exists()
        else set()
    )
    if census_path.exists():
        census = pd.read_csv(census_path, dtype={"site_no": str})
        diagnostic = set(census.loc[census["n_simple"] >= 20, "site_no"].map(_site))
    else:
        diagnostic = set()
    return legacy114, diagnostic


def _tier(row: pd.Series, cfg) -> str:
    rules = _audit(cfg)
    if bool(row.get("diagnostic_quality", False)):
        return "T4_diagnostic"
    if row.get("paired_coverage", 0) >= float(rules["core_min_paired_coverage"]) and row.get(
        "valid_years", 0
    ) >= int(rules["core_min_valid_years"]):
        return "T3_hourly_core"
    if row.get("paired_valid_hours", 0) >= int(rules["partial_min_paired_hours"]):
        return "T2_hourly_partial"
    if row.get("paired_valid_hours", 0) > 0:
        return "T1_overlap"
    return "T0_catalog_only"


def _manifest(hourly_dir: Path, cfg) -> pd.DataFrame:
    rows = []
    revision = git_revision(cfg.root).get("commit")
    generated = utc_now()
    for path in sorted(hourly_dir.glob("*.parquet")):
        frame = read_parquet(path)
        time = pd.to_datetime(frame["time"], utc=True)
        rows.append(
            {
                "site_no": path.stem,
                "local_relative_path": path.relative_to(cfg.root).as_posix(),
                "file_size_bytes": path.stat().st_size,
                "row_count": len(frame),
                "start_time": time.min(),
                "end_time": time.max(),
                "q_valid_hours": int(frame["q_valid"].sum()),
                "sc_valid_hours": int(frame["sc_valid"].sum()),
                "paired_valid_hours": int(frame["paired_valid"].sum()),
                "sha256": sha256_file(path),
                "generated_at": generated,
                "config_sha256": _config_hash(cfg),
                "git_commit": revision,
            }
        )
    return pd.DataFrame(rows)


def build_report(cfg) -> dict[str, Any]:
    catalog = read_parquet(cfg.data_dir / "interim" / "training_audit" / "catalog.parquet")
    hourly_dir = cfg.data_dir / "processed" / "training_audit" / "hourly"
    metrics = []
    if hourly_dir.exists():
        for path in sorted(hourly_dir.glob("*.parquet")):
            record = {"site_no": path.stem}
            record.update(_station_metrics(read_parquet(path), cfg))
            metrics.append(record)
    metric_frame = pd.DataFrame(metrics)
    if metric_frame.empty:
        metric_frame = pd.DataFrame({"site_no": pd.Series(dtype=str)})
    availability = catalog.merge(metric_frame, on="site_no", how="left")
    numeric_defaults = [
        "q_valid_hours",
        "sc_valid_hours",
        "paired_valid_hours",
        "paired_valid_hours_sensitivity",
        "paired_coverage",
        "paired_coverage_sensitivity",
        "valid_years",
        "longest_continuous_paired_hours",
        "longest_missing_paired_hours",
    ]
    for column in numeric_defaults:
        if column not in availability:
            availability[column] = 0
        availability[column] = availability[column].fillna(0)
    availability["actual_overlap"] = availability["paired_valid_hours"] > 0
    availability["hourly_usable"] = availability["paired_valid_hours"] >= int(
        _audit(cfg)["partial_min_paired_hours"]
    )
    legacy114, diagnostic = _legacy_membership(cfg.root)
    availability["legacy_114_member"] = availability["site_no"].isin(legacy114)
    availability["legacy_diagnostic_data_sufficient_member"] = availability["site_no"].isin(
        diagnostic
    )
    availability["diagnostic_quality"] = availability["actual_overlap"] & availability[
        "site_no"
    ].isin(diagnostic)
    availability["training_tier"] = availability.apply(lambda row: _tier(row, cfg), axis=1)
    failures = []
    for row in availability.itertuples(index=False):
        reasons = []
        if not row.catalog_q:
            reasons.append("catalog_q_missing")
        if not row.catalog_sc:
            reasons.append("catalog_sc_missing")
        if row.catalog_paired and not row.window_overlap:
            reasons.append("no_catalog_overlap_in_window")
        if row.window_overlap and not row.actual_overlap:
            reasons.append("not_downloaded_or_no_actual_paired_hour")
        if row.actual_overlap and not row.hourly_usable:
            reasons.append("paired_hours_below_partial_threshold")
        if not row.forcing_ready:
            reasons.append(str(row.forcing_missing_reason) or "forcing_not_ready")
        failures.append(";".join(reasons))
    availability["all_failed_conditions"] = failures
    availability["dam_count"] = availability["NDAMS_2009"]
    availability["dam_density"] = availability["DDENS_2009"]
    availability["gages2_class"] = availability["CLASS"]
    availability["nested_basin_flag"] = availability.duplicated("huc8", keep=False)
    availability["spatial_dependence_rule"] = (
        "same HUC8 is a flag only; nesting requires boundary analysis"
    )

    results = ensure_dir(cfg.results_dir)
    availability.to_csv(results / "station_availability.csv", index=False)
    write_parquet(availability, results / "station_availability.parquet")
    manifest = _manifest(hourly_dir, cfg) if hourly_dir.exists() else pd.DataFrame()
    manifest.to_csv(results / "hourly_files_manifest.csv", index=False)

    union = pd.Series(True, index=availability.index)
    layers = [
        ("catalog_q", availability["catalog_q"], "catalog_union", union),
        ("catalog_sc", availability["catalog_sc"], "catalog_union", union),
        ("catalog_paired", availability["catalog_paired"], "catalog_union", union),
        (
            "window_overlap",
            availability["window_overlap"],
            "catalog_paired",
            availability["catalog_paired"],
        ),
        (
            "actual_overlap",
            availability["actual_overlap"],
            "window_overlap",
            availability["window_overlap"],
        ),
        (
            "hourly_usable",
            availability["hourly_usable"],
            "actual_overlap",
            availability["actual_overlap"],
        ),
        (
            "forcing_ready",
            availability["hourly_usable"] & availability["forcing_ready"],
            "hourly_usable",
            availability["hourly_usable"],
        ),
        (
            "diagnostic_quality",
            availability["diagnostic_quality"],
            "actual_overlap",
            availability["actual_overlap"],
        ),
    ]
    funnel_rows = []
    for layer, mask, parent_layer, parent_mask in layers:
        current = set(availability.loc[mask.fillna(False), "site_no"])
        parent = set(availability.loc[parent_mask.fillna(False), "site_no"])
        dropped = parent - current
        reason_counts: Counter[str] = Counter()
        for text in availability.loc[
            availability["site_no"].isin(dropped), "all_failed_conditions"
        ]:
            reason_counts.update(item for item in str(text).split(";") if item)
        funnel_rows.append(
            {
                "layer": layer,
                "relative_to": parent_layer,
                "station_count": len(current),
                "dropped_from_parent": len(dropped),
                "drop_reasons_json": json.dumps(reason_counts, sort_keys=True),
            }
        )
    funnel = pd.DataFrame(funnel_rows)
    funnel.to_csv(results / "audit_funnel.csv", index=False)

    rules = _audit(cfg)
    sensitivity = []
    for coverage in rules["sensitivity_coverages"]:
        for years in rules["sensitivity_years"]:
            sensitivity.append(
                {
                    "paired_coverage_threshold": coverage,
                    "minimum_valid_years": years,
                    "station_count": int(
                        (
                            (availability["paired_coverage"] >= coverage)
                            & (availability["valid_years"] >= years)
                        ).sum()
                    ),
                }
            )
    pd.DataFrame(sensitivity).to_csv(results / "threshold_sensitivity.csv", index=False)
    summary = {
        "generated_at": utc_now(),
        "config_sha256": _config_hash(cfg),
        "git": git_revision(cfg.root),
        "scope": "CONUS states configured; 2015-2024; IV Q=00060 and SC=00095",
        "counts": dict(zip(funnel["layer"], funnel["station_count"], strict=True)),
        "training_tiers": availability["training_tier"].value_counts().to_dict(),
        "downloaded_hourly_files": len(manifest),
        "legacy_114_found_in_new_catalog": int(availability["legacy_114_member"].sum()),
        "legacy_diagnostic_data_sufficient_found": int(
            availability["legacy_diagnostic_data_sufficient_member"].sum()
        ),
        "limitations": [
            "actual-overlap and hourly tiers only reflect station files downloaded locally",
            "basin boundaries and nationwide forcings were not downloaded by this stage",
            "same-HUC flags do not prove nested catchments or correlation",
        ],
    }
    provenance_path = results / "data_provenance.jsonl"
    errors_path = results / "errors.jsonl"
    provenance_lines = (
        [line for line in provenance_path.read_text(encoding="utf-8").splitlines() if line]
        if provenance_path.exists()
        else []
    )
    error_lines = (
        [line for line in errors_path.read_text(encoding="utf-8").splitlines() if line]
        if errors_path.exists()
        else []
    )
    write_json(
        results / "provenance_summary.json",
        {
            "catalog_state_files": len(list((cfg.data_dir / "raw" / "catalog").glob("*.rdb.gz"))),
            "iv_requests_recorded": len(provenance_lines),
            "errors_recorded": len(error_lines),
            "configured_retries_per_request": cfg.data.retries,
            "configured_timeout_seconds": cfg.data.request_timeout_s,
            "code_version": git_revision(cfg.root),
        },
    )
    write_json(results / "audit_summary.json", summary)
    _write_audit_report(results / "AUDIT_REPORT.md", summary, funnel, availability)
    return summary


def _write_audit_report(
    path: Path, summary: dict, funnel: pd.DataFrame, availability: pd.DataFrame
) -> None:
    def markdown(frame: pd.DataFrame) -> str:
        columns = [str(column) for column in frame.columns]
        rows = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
        rows.extend(
            "| " + " | ".join(str(value) for value in row) + " |"
            for row in frame.itertuples(index=False, name=None)
        )
        return "\n".join(rows)

    table = markdown(funnel)
    tier_frame = (
        availability["training_tier"]
        .value_counts()
        .rename_axis("tier")
        .reset_index(name="stations")
    )
    tiers = markdown(tier_frame)
    legacy_count = summary["legacy_114_found_in_new_catalog"]
    diagnostic_count = summary["legacy_diagnostic_data_sufficient_found"]
    text = f"""# Training-data audit report

Generated: {summary["generated_at"]}

This audit starts from the full configured state catalogs. It does **not** use
area, GAGES-II membership, disturbance, three complete years, 80% paired
coverage, or hysteresis geometry as pre-catalog exclusion rules.

## Audit funnel

{table}

`window_overlap` is catalog evidence only. `actual_overlap` requires at least
one paired valid UTC hour from downloaded observations. Therefore incomplete
remote execution lowers the latter count and is not interpreted as absence.

## Training tiers

{tiers}

## Relationship to the earlier cohorts

- Earlier 114-station candidate members present in the new catalog: {legacy_count}.
- Earlier event-data-sufficient diagnostic members present: {diagnostic_count}.
- These memberships are annotations, never the new population ceiling.

## Current limitations

{chr(10).join(f"- {item}" for item in summary["limitations"])}

Approved observations define the primary masks. Provisional observations are
retained in sensitivity columns. No cross-hour interpolation or forward fill
is performed.
"""
    path.write_text(text, encoding="utf-8")
