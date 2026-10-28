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
import re
import shutil
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from .api import fetch_iv_batch
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
    """Return the independent training-screening rules.

    ``audit`` remains a compatibility field for configurations created before
    the nationwide screening protocol was separated from round-1 QC.
    """
    rules = getattr(cfg, "screening", None) or cfg.audit
    if not rules:
        raise ValueError("configuration has no `screening` section")
    return rules


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


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    if size < 1:
        raise ValueError("batch size must be at least one")
    return [values[i : i + size] for i in range(0, len(values), size)]


_DOWNLOAD_COLUMNS = [
    "site_no",
    "year",
    "status",
    "rows",
    "raw_relative_path",
    "raw_sha256",
    "request_sha256",
    "request_url",
    "attempted_at",
    "completed_at",
    "retries_configured",
    "message",
]
_FAILED_COLUMNS = [
    "site_no",
    "year",
    "status",
    "request_sites",
    "attempted_at",
    "retries_configured",
    "error",
]


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    ensure_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _upsert_download_manifest(path: Path, rows: list[dict[str, Any]]) -> pd.DataFrame:
    previous = pd.read_csv(path, dtype={"site_no": str}) if path.exists() else pd.DataFrame()
    update = pd.DataFrame(rows)
    combined = pd.concat([previous, update], ignore_index=True) if not previous.empty else update
    for column in _DOWNLOAD_COLUMNS:
        if column not in combined:
            combined[column] = pd.NA
    combined["site_no"] = combined["site_no"].map(_site)
    combined = combined[_DOWNLOAD_COLUMNS].drop_duplicates(["site_no", "year"], keep="last")
    # A resume hit must not erase how the site-year was originally obtained:
    # `cached` documents this run, while success/no_data document the data.
    if not previous.empty:
        original = (
            previous.assign(site_no=previous["site_no"].map(_site))
            .drop_duplicates(["site_no", "year"], keep="last")
            .set_index(["site_no", "year"])["status"]
        )
        keys = pd.MultiIndex.from_frame(combined[["site_no", "year"]])
        prior = original.reindex(keys).to_numpy()
        reused = combined["status"].eq("cached") & pd.Series(
            np.isin(prior, ["success", "no_data"]), index=combined.index
        )
        combined.loc[reused, "status"] = prior[reused.to_numpy()]
        combined.loc[reused, "message"] = "verified completion marker reused"
    combined = combined.sort_values(["site_no", "year"]).reset_index(drop=True)
    _atomic_csv(combined, path)
    return combined


def _append_failed_requests(path: Path, rows: list[dict[str, Any]]) -> pd.DataFrame:
    previous = pd.read_csv(path, dtype={"site_no": str}) if path.exists() else pd.DataFrame()
    update = pd.DataFrame(rows)
    combined = pd.concat([previous, update], ignore_index=True) if not previous.empty else update
    for column in _FAILED_COLUMNS:
        if column not in combined:
            combined[column] = pd.NA
    combined = combined[_FAILED_COLUMNS]
    _atomic_csv(combined, path)
    return combined


def _has_resumable_marker(marker: Path, raw_path: Path) -> bool:
    if not marker.exists():
        return False
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    rows = int(metadata.get("n_rows", -1))
    if rows == 0:
        return True
    if metadata.get("raw_pruned"):
        return True
    if rows < 0 or not raw_path.exists():
        return False
    expected = metadata.get("raw_sha256")
    return not expected or sha256_file(raw_path) == expected


async def _download_training_async(
    cfg,
    *,
    refresh: bool = False,
    resume: bool = True,
    cache_only: bool = False,
    site_limit: int | None = None,
    site_offset: int = 0,
    site_numbers: list[str] | None = None,
    years: list[int] | None = None,
    batch_sites: int | None = None,
    raw_root: Path | None = None,
    results_dir: Path | None = None,
    min_free_disk_gb: float | None = None,
    prune_new_verified_raw: bool | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    catalog = read_parquet(cfg.data_dir / "interim" / "training_audit" / "catalog.parquet")
    # This predicate is the frozen nationwide candidate-pool contract.
    sites = catalog[catalog["window_overlap"]].sort_values("site_no")["site_no"].map(_site).tolist()
    candidate_pool_size = len(sites)
    if site_numbers is not None:
        requested = [_site(site_no) for site_no in site_numbers]
        outside_pool = sorted(set(requested) - set(sites))
        if outside_pool:
            raise ValueError(f"sites outside frozen window_overlap pool: {outside_pool}")
        sites = requested
    elif site_offset:
        sites = sites[int(site_offset) :]
    if site_limit is not None:
        sites = sites[:site_limit]
    years = years or list(cfg.window.years)
    rules = _audit(cfg)
    raw_root = ensure_dir(raw_root or cfg.data_dir / "raw" / "training_audit" / "iv")
    results_dir = ensure_dir(results_dir or cfg.results_dir)
    manifest_path = results_dir / "download_manifest.csv"
    failed_path = results_dir / "failed_requests.csv"
    if not failed_path.exists():
        _append_failed_requests(failed_path, [])
    batch_size = int(batch_sites or cfg.data.batch_sites)
    disk_minimum = float(
        rules.get("min_free_disk_gb", 0) if min_free_disk_gb is None else min_free_disk_gb
    )
    prune = bool(
        rules.get("prune_new_verified_raw", False)
        if prune_new_verified_raw is None
        else prune_new_verified_raw
    )
    progress = {
        "requests": 0,
        "rows": 0,
        "site_years_completed": 0,
        "cached": 0,
        "no_data": 0,
        "failed": 0,
        "skipped": 0,
    }
    limits = httpx.Limits(max_connections=max(2, cfg.data.concurrency))
    async with httpx.AsyncClient(
        timeout=cfg.data.request_timeout_s,
        limits=limits,
        headers={"User-Agent": "usgs-nwis-training-audit/0.1"},
    ) as client:
        for year in years:
            pending: list[str] = []
            manifest_rows: list[dict[str, Any]] = []
            for site_no in sites:
                directory = raw_root / site_no
                marker = directory / f"{year}.complete.json"
                raw_path = directory / f"{year}.parquet"
                if resume and not refresh and _has_resumable_marker(marker, raw_path):
                    metadata = json.loads(marker.read_text(encoding="utf-8"))
                    manifest_rows.append(
                        {
                            "site_no": site_no,
                            "year": year,
                            "status": "cached",
                            "rows": int(metadata.get("n_rows", 0)),
                            "raw_relative_path": str(raw_path),
                            "raw_sha256": metadata.get("raw_sha256", ""),
                            "request_sha256": metadata.get("request_sha256", ""),
                            "attempted_at": utc_now(),
                            "completed_at": metadata.get("completed_at", ""),
                            "retries_configured": cfg.data.retries,
                            "message": "verified completion marker reused",
                        }
                    )
                    progress["cached"] += 1
                elif cache_only:
                    manifest_rows.append(
                        {
                            "site_no": site_no,
                            "year": year,
                            "status": "skipped",
                            "rows": 0,
                            "attempted_at": utc_now(),
                            "retries_configured": cfg.data.retries,
                            "message": "cache_only and no resumable cache",
                        }
                    )
                    progress["skipped"] += 1
                else:
                    pending.append(site_no)
            if manifest_rows:
                _upsert_download_manifest(manifest_path, manifest_rows)

            batches = _chunks(pending, batch_size)
            wave_size = max(1, int(cfg.data.concurrency))
            disk_stop = False
            for wave in _chunks(batches, wave_size):
                free_gb = shutil.disk_usage(raw_root).free / 1_000_000_000
                if free_gb < disk_minimum:
                    skipped_rows = [
                        {
                            "site_no": site_no,
                            "year": year,
                            "status": "skipped",
                            "rows": 0,
                            "attempted_at": utc_now(),
                            "retries_configured": cfg.data.retries,
                            "message": f"disk guard: {free_gb:.2f} GB < {disk_minimum:.2f} GB",
                        }
                        for batch in wave
                        for site_no in batch
                    ]
                    _upsert_download_manifest(manifest_path, skipped_rows)
                    progress["skipped"] += len(skipped_rows)
                    disk_stop = True
                    break
                attempted_at = utc_now()
                outcomes = await asyncio.gather(
                    *(
                        fetch_iv_batch(
                            client,
                            cfg.data.iv_service,
                            batch,
                            list(cfg.params.values()),
                            f"{year}-01-01",
                            f"{year}-12-31",
                            retries=cfg.data.retries,
                        )
                        for batch in wave
                    ),
                    return_exceptions=True,
                )
                for batch, outcome in zip(wave, outcomes, strict=True):
                    if isinstance(outcome, BaseException):
                        error = str(outcome)
                        failed_rows = [
                            {
                                "site_no": site_no,
                                "year": year,
                                "status": "failed",
                                "request_sites": "|".join(batch),
                                "attempted_at": attempted_at,
                                "retries_configured": cfg.data.retries,
                                "error": error,
                            }
                            for site_no in batch
                        ]
                        _append_failed_requests(failed_path, failed_rows)
                        _upsert_download_manifest(
                            manifest_path,
                            [
                                {
                                    "site_no": row["site_no"],
                                    "year": year,
                                    "status": "failed",
                                    "rows": 0,
                                    "attempted_at": attempted_at,
                                    "retries_configured": cfg.data.retries,
                                    "message": error,
                                }
                                for row in failed_rows
                            ],
                        )
                        progress["failed"] += len(batch)
                        continue
                    result = outcome
                    append_jsonl(
                        results_dir / "data_provenance.jsonl",
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
                    completed_rows = []
                    for site_no in batch:
                        directory = ensure_dir(raw_root / site_no)
                        raw_path = directory / f"{year}.parquet"
                        part = result.frame[result.frame["site_no"].map(_site) == site_no].copy()
                        status = "no_data" if part.empty else "success"
                        raw_sha256 = ""
                        raw_pruned = False
                        if not part.empty:
                            write_parquet(part, raw_path)
                            # Verification precedes both the complete marker and optional pruning.
                            verified = read_parquet(raw_path)
                            if len(verified) != len(part):
                                raise RuntimeError(f"raw verification failed for {site_no}-{year}")
                            raw_sha256 = sha256_file(raw_path)
                            if prune:
                                raw_path.unlink()
                                raw_pruned = True
                        metadata = {
                            "site_no": site_no,
                            "year": year,
                            "status": status,
                            "n_rows": len(part),
                            "raw_sha256": raw_sha256,
                            "raw_pruned": raw_pruned,
                            "request_sha256": result.sha256,
                            "completed_at": utc_now(),
                        }
                        write_json(directory / f"{year}.complete.json", metadata)
                        completed_rows.append(
                            {
                                "site_no": site_no,
                                "year": year,
                                "status": status,
                                "rows": len(part),
                                "raw_relative_path": str(raw_path),
                                "raw_sha256": raw_sha256,
                                "request_sha256": result.sha256,
                                "request_url": result.url,
                                "attempted_at": attempted_at,
                                "completed_at": metadata["completed_at"],
                                "retries_configured": cfg.data.retries,
                                "message": "new verified raw pruned" if raw_pruned else "",
                            }
                        )
                        progress[status if status == "no_data" else "site_years_completed"] += 1
                    _upsert_download_manifest(manifest_path, completed_rows)
                    progress["requests"] += 1
                    progress["rows"] += result.n_rows
                if on_progress is not None:
                    on_progress({"year": year, **progress})
            if disk_stop:
                # Stop cleanly: markers for everything already fetched stay valid, so
                # rerunning the same command after freeing space resumes here.
                progress["disk_stop_year"] = year
                break
    progress.update(
        {
            "candidate_pool_window_overlap": candidate_pool_size,
            "sites_selected": len(sites),
            "years": years,
            "batch_sites": batch_size,
            "resume": resume,
            "cache_only": cache_only,
            "min_free_disk_gb": disk_minimum,
            "prune_new_verified_raw": prune,
            "finished_at": utc_now(),
        }
    )
    write_json(results_dir / "download_summary.json", progress)
    return progress


def download_training(
    cfg,
    *,
    refresh: bool = False,
    resume: bool = True,
    cache_only: bool = False,
    site_limit: int | None = None,
    site_offset: int = 0,
    site_numbers: list[str] | None = None,
    years: list[int] | None = None,
    batch_sites: int | None = None,
    raw_root: Path | None = None,
    results_dir: Path | None = None,
    min_free_disk_gb: float | None = None,
    prune_new_verified_raw: bool | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        _download_training_async(
            cfg,
            refresh=refresh,
            resume=resume,
            cache_only=cache_only,
            site_limit=site_limit,
            site_offset=site_offset,
            site_numbers=site_numbers,
            years=years,
            batch_sites=batch_sites,
            raw_root=raw_root,
            results_dir=results_dir,
            min_free_disk_gb=min_free_disk_gb,
            prune_new_verified_raw=prune_new_verified_raw,
            on_progress=on_progress,
        )
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


def _qualifier_tokens(value: Any) -> frozenset[str]:
    """Parse USGS qualifiers without substring matching.

    WaterML supplies a list, but older cached frames may contain delimited
    strings or the historical compact form ``Ae``. Only compact strings made
    entirely from the known one-character A/P/e tokens are split; words such
    as ``ICE`` therefore remain one token and cannot accidentally approve or
    reject an observation.
    """
    if value is None or (not isinstance(value, (list, tuple, set)) and pd.isna(value)):
        return frozenset()
    if isinstance(value, (list, tuple, set, frozenset)):
        tokens: set[str] = set()
        for item in value:
            tokens.update(_qualifier_tokens(item))
        return frozenset(tokens)
    text = str(value).strip()
    if not text:
        return frozenset()
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            return _qualifier_tokens(decoded)
    pieces = [item for item in re.split(r"[,|;\s]+", text) if item]
    if len(pieces) == 1 and re.fullmatch(r"[APe]+", text):
        pieces = list(text)
    normalized = {"e" if item.lower() == "e" else item.upper() for item in pieces}
    return frozenset(normalized)


def _qualifier_class(value: Any, cfg=None) -> str:
    tokens = _qualifier_tokens(value)
    qualifier_rules = _audit(cfg).get("qualifier_tokens", {}) if cfg is not None else {}
    approved = str(qualifier_rules.get("approved", "A")).upper()
    estimated = str(qualifier_rules.get("estimated", "e")).lower()
    rejected = {str(item).upper() for item in qualifier_rules.get("rejected", ["P"])}
    if tokens & rejected:
        return "rejected"
    if approved in tokens:
        return "approved_estimated" if estimated in tokens else "approved"
    return "other"


def _hourly_param(part: pd.DataFrame, prefix: str, cfg) -> tuple[pd.DataFrame, dict[int, float]]:
    rules = _audit(cfg)
    cadence = _cadence_by_year(part)
    value_range = rules["q_valid_range"] if prefix == "q" else rules["sc_valid_range_us_cm"]
    part = part.copy()
    part["qual_tokens"] = part["qualifiers"].map(_qualifier_tokens)
    part["qual_class"] = part["qualifiers"].map(lambda value: _qualifier_class(value, cfg))
    part["range_ok"] = part["value"].between(value_range[0], value_range[1], inclusive="both")
    if prefix == "sc":
        part["range_ok"] &= part["value"].gt(0)
    part["approved_ok"] = part["range_ok"] & part["qual_class"].isin(
        ["approved", "approved_estimated"]
    )
    part["estimated_ok"] = part["approved_ok"] & part["qual_class"].eq("approved_estimated")
    # Retained for output compatibility. Provisional P is rejected in every
    # mask; the sensitivity column currently equals the approved mask.
    part["sensitivity_ok"] = part["approved_ok"]
    part["hour"] = part["time"].dt.floor("h")

    # Vectorised group-by aggregations instead of a Python function per hour:
    # the national run aggregates ~1,000 station-decades, where a per-hour
    # ``apply`` dominates wall clock.  Column semantics are unchanged.
    grouped = part.groupby("hour", sort=True)
    approved = part.loc[part["approved_ok"]].groupby("hour", sort=True)["value"]
    estimated = part.loc[part["estimated_ok"]].groupby("hour", sort=True)["time"]
    sensitivity = part.loc[part["sensitivity_ok"]].groupby("hour", sort=True)["value"]
    sensitivity_time = part.loc[part["sensitivity_ok"]].groupby("hour", sort=True)["time"]
    approved_time = part.loc[part["approved_ok"]].groupby("hour", sort=True)["time"]
    flags = (
        part[["hour", "qualifiers"]]
        .assign(text=part["qualifiers"].fillna("").astype(str))
        .drop(columns=["qualifiers"])
        .drop_duplicates()
        .sort_values(["hour", "text"])
        .groupby("hour", sort=True)["text"]
        .agg("|".join)
    )
    summary = pd.DataFrame(
        {
            f"{prefix}_value": approved.mean(),
            f"{prefix}_median": approved.median(),
            f"{prefix}_std": approved.std(ddof=0),
            f"{prefix}_min": approved.min(),
            f"{prefix}_max": approved.max(),
            f"{prefix}_native_samples": grouped.size(),
            f"{prefix}_unique_samples": grouped["time"].nunique(),
            f"{prefix}_approved_samples": grouped["approved_ok"].sum(),
            f"{prefix}_approved_unique_samples": approved_time.nunique(),
            f"{prefix}_estimated_samples": grouped["estimated_ok"].sum(),
            f"{prefix}_estimated_unique_samples": estimated.nunique(),
            f"{prefix}_rejected_samples": part.assign(rejected=part["qual_class"].eq("rejected"))
            .groupby("hour")["rejected"]
            .sum(),
            f"{prefix}_sensitivity_samples": grouped["sensitivity_ok"].sum(),
            f"{prefix}_sensitivity_unique_samples": sensitivity_time.nunique(),
            f"{prefix}_value_sensitivity": sensitivity.mean(),
            f"{prefix}_quality_flags": flags,
            f"{prefix}_duplicate_timestamps": grouped.size() - grouped["time"].nunique(),
            f"{prefix}_negative_samples": part.assign(negative=part["value"] < 0)
            .groupby("hour")["negative"]
            .sum(),
            f"{prefix}_nonpositive_samples": part.assign(nonpositive=part["value"] <= 0)
            .groupby("hour")["nonpositive"]
            .sum(),
            f"{prefix}_range_failure_samples": part.assign(
                failure=(~part["range_ok"]) & part["value"].notna()
            )
            .groupby("hour")["failure"]
            .sum(),
        }
    )
    summary.index.name = "time"
    return summary, cadence


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
            "approved_unique_samples",
            "estimated_samples",
            "estimated_unique_samples",
            "rejected_samples",
            "sensitivity_samples",
            "sensitivity_unique_samples",
            "duplicate_timestamps",
            "negative_samples",
            "nonpositive_samples",
            "range_failure_samples",
        ):
            column = f"{prefix}_{name}"
            if column not in hourly:
                hourly[column] = 0
            else:
                hourly[column] = hourly[column].fillna(0)
        quality_column = f"{prefix}_quality_flags"
        if quality_column not in hourly:
            hourly[quality_column] = ""
        expected = pd.Series(hourly.index.year, index=hourly.index).map(
            {year: max(1.0, round(60.0 / value)) for year, value in cadence.items() if value > 0}
        )
        hourly[f"{prefix}_expected_samples"] = expected
        hourly[f"{prefix}_hour_coverage"] = (
            hourly.get(f"{prefix}_approved_unique_samples", 0) / expected
        ).clip(upper=1)
        hourly[f"{prefix}_sensitivity_coverage"] = (
            hourly.get(f"{prefix}_sensitivity_unique_samples", 0) / expected
        ).clip(upper=1)
        minimum_coverage = float(
            _audit(cfg).get(
                "hourly_min_unique_timestamp_coverage",
                _audit(cfg).get("hourly_min_coverage", 0.75),
            )
        )
        hourly[f"{prefix}_valid"] = hourly[f"{prefix}_hour_coverage"].fillna(0).ge(minimum_coverage)
        hourly[f"{prefix}_valid_sensitivity"] = (
            hourly[f"{prefix}_sensitivity_coverage"].fillna(0).ge(minimum_coverage)
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


def _aggregate_one(job: tuple[str, str, str, Any]) -> tuple[str, str]:
    """Worker for :func:`build_hourly`; module level so Windows spawn can import it."""
    site_no, raw_root, out_root, cfg = job
    files = sorted((Path(raw_root) / site_no).glob("*.parquet"))
    if not files:
        return site_no, "no_raw"
    raw = pd.concat([read_parquet(path) for path in files], ignore_index=True)
    hourly = aggregate_site_hourly(raw, cfg)
    write_parquet(hourly, Path(out_root) / f"{site_no}.parquet")
    return site_no, "written"


def build_hourly(
    cfg,
    *,
    site_limit: int | None = None,
    refresh: bool = False,
    raw_root: Path | None = None,
    out_root: Path | None = None,
    results_dir: Path | None = None,
    workers: int = 1,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    raw_root = raw_root or cfg.data_dir / "raw" / "training_audit" / "iv"
    out_root = ensure_dir(out_root or cfg.data_dir / "processed" / "training_audit" / "hourly")
    sites = (
        sorted(path.name for path in raw_root.iterdir() if path.is_dir())
        if raw_root.exists()
        else []
    )
    if site_limit is not None:
        sites = sites[:site_limit]
    written = 0
    skipped = 0
    todo: list[str] = []
    for site_no in sites:
        destination = out_root / f"{site_no}.parquet"
        if destination.exists() and not refresh:
            skipped += 1
            continue
        todo.append(site_no)
    jobs = [(site_no, str(raw_root), str(out_root), cfg) for site_no in todo]

    def consume(results) -> None:
        nonlocal written
        for site_no, status in results:
            if status == "written":
                written += 1
                if on_progress is not None:
                    on_progress({"written": written, "skipped": skipped, "site_no": site_no})

    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            consume(pool.map(_aggregate_one, jobs, chunksize=1))
    else:
        consume(_aggregate_one(job) for job in jobs)
    result = {"sites_written": written, "sites_skipped": skipped, "finished_at": utc_now()}
    write_json(ensure_dir(results_dir or cfg.results_dir) / "hourly_summary.json", result)
    return result


def run_network_smoke(
    cfg,
    *,
    cache_dir: Path,
    force_network: bool = False,
    year: int | None = None,
    batch_sites: int = 5,
    n_sites: int = 5,
) -> dict[str, Any]:
    """Exercise the production downloader and aggregator in an isolated cache."""
    cache_dir = ensure_dir(cache_dir)
    raw_root = ensure_dir(cache_dir / "raw")
    hourly_root = ensure_dir(cache_dir / "hourly")
    results_dir = ensure_dir(cache_dir / "results")
    chosen_year = int(year or max(cfg.window.years))
    catalog = read_parquet(cfg.data_dir / "interim" / "training_audit" / "catalog.parquet")
    candidates = catalog[catalog["window_overlap"]].copy()
    candidates["site_no"] = candidates["site_no"].map(_site)
    preferred = ["01018035", "01095220", "01095375", "01095434", "01100568"]
    chosen = [site for site in preferred if site in set(candidates["site_no"])]
    if len(chosen) < n_sites:
        candidates["smoke_records"] = candidates[["catalog_q_records", "catalog_sc_records"]].min(
            axis=1
        )
        active = candidates[
            pd.to_datetime(candidates["catalog_overlap_end"], errors="coerce")
            >= pd.Timestamp(f"{chosen_year}-01-01")
        ]
        pool = active if not active.empty else candidates
        ordered = pool.sort_values("smoke_records", ascending=False)["site_no"].tolist()
        chosen = list(dict.fromkeys([*chosen, *ordered]))[:n_sites]
    download = download_training(
        cfg,
        refresh=force_network,
        resume=not force_network,
        site_numbers=chosen,
        years=[chosen_year],
        batch_sites=batch_sites,
        raw_root=raw_root,
        results_dir=results_dir,
    )
    if force_network and int(download["requests"]) < 1:
        raise RuntimeError("forced network smoke made zero requests")
    hourly = build_hourly(
        cfg,
        refresh=True,
        raw_root=raw_root,
        out_root=hourly_root,
        results_dir=results_dir,
    )
    checks: list[dict[str, Any]] = []
    for site_no in chosen:
        raw_files = sorted((raw_root / site_no).glob("*.parquet"))
        hourly_file = hourly_root / f"{site_no}.parquet"
        record: dict[str, Any] = {
            "site_no": site_no,
            "raw_file": raw_files[0].as_posix() if raw_files else "",
            "raw_rows": 0,
            "has_q": False,
            "has_sc": False,
            "qualifier_values": "",
            "hourly_file": hourly_file.as_posix() if hourly_file.exists() else "",
            "hourly_rows": 0,
            "q_valid_hours": 0,
            "sc_valid_hours": 0,
            "paired_valid_hours": 0,
        }
        if raw_files:
            raw = read_parquet(raw_files[0])
            record["raw_rows"] = len(raw)
            record["has_q"] = bool((raw["param_cd"] == cfg.params["streamflow"]).any())
            record["has_sc"] = bool((raw["param_cd"] == cfg.params["specific_conductance"]).any())
            record["qualifier_values"] = "|".join(
                sorted(set(raw["qualifiers"].fillna("").astype(str)))[:20]
            )
        if hourly_file.exists():
            frame = read_parquet(hourly_file)
            record["hourly_rows"] = len(frame)
            record["q_valid_hours"] = int(frame["q_valid"].sum())
            record["sc_valid_hours"] = int(frame["sc_valid"].sum())
            record["paired_valid_hours"] = int(frame["paired_valid"].sum())
        checks.append(record)
    result = {
        "cache_dir": str(cache_dir),
        "force_network": force_network,
        "year": chosen_year,
        "sites": chosen,
        "download": download,
        "hourly": hourly,
        "checks": checks,
        "network_requests_positive": int(download["requests"]) > 0,
        "finished_at": utc_now(),
    }
    pd.DataFrame(checks).to_csv(results_dir / "smoke_checks.csv", index=False)
    write_json(results_dir / "smoke_summary.json", result)
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
    total = int(hourly[f"{prefix}_native_samples"].fillna(0).sum())
    if total == 0:
        return {"approved": 0.0, "estimated": 0.0, "rejected": 0.0, "other": 0.0}
    approved = int(hourly[f"{prefix}_approved_samples"].fillna(0).sum())
    estimated_column = f"{prefix}_estimated_samples"
    rejected_column = f"{prefix}_rejected_samples"
    estimated = int(hourly.get(estimated_column, pd.Series(0, index=hourly.index)).fillna(0).sum())
    rejected = int(hourly.get(rejected_column, pd.Series(0, index=hourly.index)).fillna(0).sum())
    return {
        "approved": approved / total,
        "estimated": estimated / total,
        "rejected": rejected / total,
        "other": max(0.0, 1 - (approved + rejected) / total),
    }


def _split_bounds(bounds) -> tuple[pd.Timestamp, pd.Timestamp]:
    start, end = pd.Timestamp(bounds[0]), pd.Timestamp(bounds[1])
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    return start, end


def _fixed_split_metrics(hourly: pd.DataFrame, cfg) -> dict[str, Any]:
    rules = _audit(cfg)
    minimum_day_hours = int(rules["valid_day_min_paired_hours"])
    cold_days = int(rules["valid_year_min_valid_days"]["cold"])
    warm_days = int(rules["valid_year_min_valid_days"]["warm"])
    output: dict[str, Any] = {}
    for split, bounds in rules["splits"].items():
        start, end = _split_bounds(bounds)
        part = hourly[hourly["time"].between(start, end, inclusive="both")].copy()
        expected_hours = len(pd.date_range(start.floor("h"), end.floor("h"), freq="h"))
        daily = part.assign(day=part["time"].dt.floor("D")).groupby("day")["paired_valid"].sum()
        days = pd.date_range(start.floor("D"), end.floor("D"), freq="D")
        daily = daily.reindex(days, fill_value=0)
        valid_days = daily.ge(minimum_day_hours)
        # Valid years are calendar years: cold = Jan-Mar + Dec, warm = Apr-Nov.
        q_valid_by_day = (
            part.assign(day=part["time"].dt.floor("D"))
            .groupby("day")["q_valid"]
            .sum()
            .reindex(days, fill_value=0)
        )
        expected_by_day = pd.Series(24, index=days, dtype=float)
        annual_valid_years = 0
        annual_q_coverage: dict[str, dict[str, float]] = {}
        split_cold_days = 0
        split_warm_days = 0
        for year, index in valid_days.groupby(valid_days.index.year).groups.items():
            year_mask = valid_days.loc[index]
            cold = year_mask[(year_mask.index.month <= 3) | (year_mask.index.month == 12)].sum()
            warm = year_mask[(year_mask.index.month >= 4) & (year_mask.index.month <= 11)].sum()
            split_cold_days += int(cold)
            split_warm_days += int(warm)
            is_valid = bool(cold >= cold_days and warm >= warm_days)
            annual_valid_years += int(is_valid)
            expected_year = float(expected_by_day.loc[index].sum())
            covered = float(q_valid_by_day.loc[index].sum())
            annual_q_coverage[str(year)] = {
                "q_valid_hours": covered,
                "expected_hours": expected_year,
                "q_coverage": covered / expected_year if expected_year else 0.0,
                "valid_days_cold": int(cold),
                "valid_days_warm": int(warm),
                "valid_year": is_valid,
            }
        for name in ("q_valid", "sc_valid", "paired_valid"):
            count = int(part[name].fillna(False).sum()) if name in part else 0
            output[f"{split}_{name}_hours"] = count
            output[f"{split}_{name.replace('_valid', '')}_coverage"] = (
                count / expected_hours if expected_hours else 0.0
            )
        output[f"{split}_expected_hours"] = expected_hours
        output[f"{split}_valid_days"] = int(valid_days.sum())
        output[f"{split}_valid_days_cold"] = split_cold_days
        output[f"{split}_valid_days_warm"] = split_warm_days
        output[f"{split}_valid_years"] = int(annual_valid_years)
        output[f"{split}_valid_days_by_year_json"] = json.dumps(
            {str(year): int(count) for year, count in daily.groupby(daily.index.year).sum().items()}
        )
        output[f"{split}_q_coverage_by_year_json"] = json.dumps(annual_q_coverage)
    return output


def _state_phase_metrics(hourly: pd.DataFrame, cfg) -> tuple[dict[str, float], dict[str, int]]:
    rules = _audit(cfg)
    quantile_levels = rules.get("flow_state_quantiles", [0.30, 0.70])
    smooth_hours = int(rules.get("phase_smoothing_hours", 3))
    qvalid = hourly["q_valid"].fillna(False)
    paired = hourly["paired_valid"].fillna(False)
    train_bounds = rules["splits"]["train"]
    train_start, train_end = _split_bounds(train_bounds)
    train_q = hourly.loc[
        hourly["time"].between(train_start, train_end, inclusive="both") & qvalid,
        "q_value",
    ].dropna()
    reference = train_q if not train_q.empty else hourly.loc[qvalid, "q_value"].dropna()
    if reference.empty:
        return {}, {}
    low, high = reference.quantile(quantile_levels).tolist()
    q = hourly["q_value"]
    # Status is defined on the 3-hour median-smoothed flow, not raw hour-to-hour noise.
    q_smooth = q.rolling(smooth_hours, center=True, min_periods=1).median()
    state_masks = {
        "low": q.le(low),
        "mid": q.gt(low) & q.lt(high),
        "high": q.ge(high),
    }
    dq = q_smooth.diff()
    epsilon = float(rules.get("phase_change_fraction", 0.01)) * q_smooth.abs().clip(lower=1e-9)
    phase_masks = {
        "rising": dq.gt(epsilon),
        "falling": dq.lt(-epsilon),
        "stable": dq.abs().le(epsilon),
    }
    counts: dict[str, int] = {}
    for split, bounds in rules["splits"].items():
        split_start, split_end = _split_bounds(bounds)
        split_mask = hourly["time"].between(split_start, split_end, inclusive="both")
        for name, mask in state_masks.items():
            counts[f"{split}_{name}_state_paired_hours"] = int((split_mask & paired & mask).sum())
        for name, mask in phase_masks.items():
            counts[f"{split}_{name}_phase_paired_hours"] = int((split_mask & paired & mask).sum())
    for name, mask in state_masks.items():
        counts[f"all_{name}_state_paired_hours"] = int((paired & mask).sum())
    for name, mask in phase_masks.items():
        counts[f"all_{name}_phase_paired_hours"] = int((paired & mask).sum())
    counts["all_paired_hours"] = int(paired.sum())
    return {"low": float(low), "high": float(high)}, counts


def _years_q_coverage(metrics: dict[str, Any], split: str) -> list[float]:
    raw = metrics.get(f"{split}_q_coverage_by_year_json", "{}")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    return [float(item.get("q_coverage", 0)) for item in parsed.values()]


def _tier_eligibility(metrics: dict[str, Any], cfg, tier: str) -> tuple[bool, list[str]]:
    rule = _audit(cfg)["tiers"][tier]
    reasons: list[str] = []
    for split in ("train", "validation", "test"):
        split_rule = rule.get(split, {})
        minimum_years = int(split_rule.get("min_valid_years", 0))
        if int(metrics.get(f"{split}_valid_years", 0)) < minimum_years:
            reasons.append(f"{split}_valid_years")
        minimum_hours = split_rule.get("min_paired_hours")
        if minimum_hours is not None and int(metrics.get(f"{split}_paired_valid_hours", 0)) < int(
            minimum_hours
        ):
            reasons.append(f"{split}_paired_hours")
        threshold = split_rule.get("q_coverage_threshold")
        minimum_cov_years = split_rule.get("min_years_q_coverage")
        if threshold is not None and minimum_cov_years is not None:
            qualifying = sum(
                coverage >= float(threshold) for coverage in _years_q_coverage(metrics, split)
            )
            if qualifying < int(minimum_cov_years):
                reasons.append(f"{split}_years_q_coverage")
    minimum_state = int(rule.get("min_state_paired_hours", 0))
    minimum_fraction = float(rule.get("min_state_fraction_of_paired", 0))
    total_paired = max(1, int(metrics.get("all_paired_hours", 0)))
    for state in ("low", "mid", "high"):
        count = int(metrics.get(f"all_{state}_state_paired_hours", 0))
        if count < minimum_state or count / total_paired < minimum_fraction:
            reasons.append(f"{state}_state_coverage")
    minimum_phase = int(rule.get("min_phase_paired_hours", 0))
    for phase in ("rising", "falling"):
        if int(metrics.get(f"all_{phase}_phase_paired_hours", 0)) < minimum_phase:
            reasons.append(f"{phase}_phase_coverage")
    return not reasons, reasons


def _sc_anomaly_metrics(hourly: pd.DataFrame, cfg) -> dict[str, Any]:
    """Diagnostic flags only.  None of these ever removes a station or a value."""
    rules = _audit(cfg)
    frame = hourly[["time", "sc_value", "sc_valid"]].copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    grid = frame.set_index("time").sort_index()
    values = grid["sc_value"].where(grid["sc_valid"].fillna(False).astype(bool))
    # A full UTC hourly grid means diffs and centred windows are true hour steps.
    same = values.diff().eq(0)
    constant_runs = _runs(same)
    constant_run = max(constant_runs) + 1 if constant_runs else 0

    local_median = values.rolling(7, center=True, min_periods=3).median()
    excursion = (values - local_median).abs()
    recovery = (values.shift(-3) - values.shift(3)).abs()
    spike_threshold = float(rules.get("sc_spike_absolute_us_cm", 500))
    spikes = int(
        (excursion.ge(spike_threshold) & recovery.le(0.1 * spike_threshold) & values.notna()).sum()
    )

    relative_diff = values.diff().abs() / values.shift().abs().clip(lower=1)
    jumps = int(
        (
            values.diff().abs().ge(float(rules["sc_jump_absolute_us_cm"]))
            & relative_diff.ge(float(rules["sc_jump_relative_fraction"]))
        ).sum()
    )

    minimum_year_hours = int(rules.get("sc_year_min_valid_hours", 720))
    annual = values.groupby(values.index.year)
    medians = annual.median()[annual.count() >= minimum_year_hours].sort_index()
    ratios = (medians / medians.shift().abs().clip(lower=1e-9)).dropna()
    annual_ratio = float(ratios.max()) if len(ratios) else 1.0
    if len(ratios):
        annual_ratio = max(annual_ratio, float((1 / ratios).max()))

    trend_years = int(len(medians))
    trend_consistent = 0.0
    trend_change = 0.0
    if trend_years >= 2:
        steps = medians.diff().dropna()
        trend_consistent = float(max((steps > 0).mean(), (steps < 0).mean()))
        base = float(medians.abs().median()) or 1.0
        trend_change = abs(float(medians.iloc[-1] - medians.iloc[0])) / base

    cadence_change = bool(
        hourly.get("sc_native_cadence_minutes", pd.Series(dtype=float)).dropna().nunique() > 1
    )

    flags = {
        "sc_constant_gt_30d": constant_run >= int(rules["sc_constant_run_hours"]),
        "sc_isolated_spike": spikes >= int(rules.get("sc_spike_min_events", 3)),
        "sc_interannual_median_shift": annual_ratio >= float(rules["sc_year_median_ratio"]),
        "sc_long_term_monotonic_change": (
            trend_years >= int(rules.get("sc_trend_min_years", 6))
            and trend_consistent >= float(rules.get("sc_trend_sign_consistency", 0.9))
            and trend_change >= float(rules.get("sc_trend_min_relative_change", 0.50))
        ),
        "sc_instrument_or_range_switch": cadence_change,
    }
    return {
        "sc_constant_run_hours_max": int(constant_run),
        "sc_isolated_spike_events": spikes,
        "sc_jump_events": jumps,
        "sc_interannual_median_ratio_max": annual_ratio,
        "sc_annual_median_years_used": trend_years,
        "sc_annual_median_sign_consistency": trend_consistent,
        "sc_annual_median_relative_change": trend_change,
        "sc_flags_json": json.dumps(
            {name: bool(value) for name, value in flags.items()}, sort_keys=True
        ),
        "sc_anomaly_flags": "|".join(name for name, value in flags.items() if value),
        "sc_anomaly_flag_any": any(flags.values()),
        "sc_anomaly_flags_are_exclusionary": False,
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
    split_metrics = _fixed_split_metrics(hourly, cfg)
    state_quantiles, state_phase_counts = _state_phase_metrics(hourly, cfg)
    valid_years = sum(
        int(split_metrics.get(f"{split}_valid_years", 0)) for split in rules["splits"]
    )
    anomaly = _sc_anomaly_metrics(hourly, cfg)
    jumps = int(anomaly["sc_jump_events"])
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
    result = {
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
        "sc_jump_fraction": float(jumps / max(1, int(scvalid.sum()))),
        "sc_constant_suspect": anomaly["sc_constant_run_hours_max"]
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
        "sc_estimated_sample_fraction": float(
            hourly["sc_estimated_samples"].fillna(0).sum()
            / max(1, hourly["sc_native_samples"].fillna(0).sum())
        ),
        "q_estimated_sample_fraction": float(
            hourly["q_estimated_samples"].fillna(0).sum()
            / max(1, hourly["q_native_samples"].fillna(0).sum())
        ),
        "wet_dry_definition": "training-period station Q thirds; no external drought label",
        **split_metrics,
        **state_phase_counts,
    }
    # A station with no valid Q at all has no state/phase counts; every derived
    # fraction then defaults to zero rather than failing the report.
    total_paired = max(1, int(result.get("all_paired_hours", 0)))
    for state in ("low", "mid", "high"):
        result[f"all_{state}_state_paired_fraction"] = (
            int(result.get(f"all_{state}_state_paired_hours", 0)) / total_paired
        )
    for phase in ("rising", "falling", "stable"):
        result[f"all_{phase}_phase_paired_fraction"] = (
            int(result.get(f"all_{phase}_phase_paired_hours", 0)) / total_paired
        )
    result.update(anomaly)
    result["flow_state_train_quantiles_json"] = json.dumps(state_quantiles)
    for tier in ("core", "extended"):
        eligible, reasons = _tier_eligibility(result, cfg, tier)
        result[f"{tier}_eligible"] = eligible
        result[f"{tier}_failed_criteria"] = ";".join(reasons)
    return result


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
    """Assemble the national availability table, tiers, funnel and report.

    Imported lazily: :mod:`national_report` reuses the station metrics defined
    here, so a module-level import would be circular.
    """
    from .national_report import build_report as _build_report

    return _build_report(cfg)
