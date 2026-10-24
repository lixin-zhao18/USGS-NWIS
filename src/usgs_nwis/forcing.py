"""Forcing preparation for the A-class hysteresis pilot.

AORC v1.1 (1-km, hourly) is read anonymously from the NOAA Open Data S3 Zarr
store. NLDAS-2 (0.125 deg, hourly) requires a NASA Earthdata login and is not
downloaded here until credentials are configured.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .io import append_jsonl, ensure_dir, read_parquet, utc_now, write_json

AORC_BUCKET = "s3://noaa-nws-aorc-v1-1-1km"
AORC_VARIABLES = [
    "APCP_surface",
    "TMP_2maboveground",
    "SPFH_2maboveground",
    "PRES_surface",
    "DLWRF_surface",
    "DSWRF_surface",
    "UGRD_10maboveground",
    "VGRD_10maboveground",
]
CORE_VARIABLES = [
    "APCP_surface",
    "TMP_2maboveground",
    "SPFH_2maboveground",
    "DSWRF_surface",
]


def a_cohort(cfg) -> pd.DataFrame:
    """Independent stations with at least 20 simple loops."""
    from .selection import _river_key

    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    candidates["site_no"] = candidates["site_no"].astype(str)
    candidates = candidates[candidates["candidate_ok"]].copy()
    census = pd.read_csv(
        cfg.results_dir / "census_station_summary.csv", dtype={"site_no": str}
    )
    frame = candidates.merge(census, on="site_no", how="left", suffixes=("", "_census"))
    frame["huc8"] = frame["huc"].astype(str).str[:8]
    pool = frame[frame["n_simple"] >= 20].sort_values(
        ["reproducible", "n_simple"], ascending=False
    )
    huc_counts: dict[str, int] = {}
    river_keys: set[tuple[str, str]] = set()
    chosen: list[str] = []
    for row in pool.itertuples():
        key = (row.huc8, _river_key(row.station_name))
        if huc_counts.get(row.huc8, 0) >= 2 or key in river_keys:
            continue
        chosen.append(row.site_no)
        huc_counts[row.huc8] = huc_counts.get(row.huc8, 0) + 1
        river_keys.add(key)
    return frame[frame["site_no"].isin(chosen)].copy()


def a_class_events(cfg) -> pd.DataFrame:
    events = pd.read_csv(
        cfg.results_dir / "census_event_metrics.csv", dtype={"site_no": str}
    )
    events = events[events["class"] == "A_simple_closed"].copy()
    cohort = a_cohort(cfg)[["site_no", "lat", "lon", "stratum", "n_simple"]]
    events = events.merge(cohort, on="site_no", how="inner")
    events["start"] = pd.to_datetime(events["start"], utc=True)
    events["end"] = pd.to_datetime(events["end"], utc=True)
    events["peak"] = pd.to_datetime(events["peak"], utc=True)
    events["forcing_start"] = events["start"] - pd.Timedelta(hours=96)
    events["forcing_end"] = events["end"] + pd.Timedelta(hours=12)
    return events.sort_values(["site_no", "start"]).reset_index(drop=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _extract_aorc_station(
    site_no: str,
    lat: float,
    lon: float,
    years: list[int],
    variables: list[str],
    out_dir: Path,
) -> dict:
    import xarray as xr

    frames: list[pd.DataFrame] = []
    for year in years:
        store = f"{AORC_BUCKET}/{year}.zarr"
        dataset = xr.open_zarr(
            store, consolidated=True, storage_options={"anon": True}
        )
        try:
            subset = (
                dataset[variables]
                .sel(latitude=lat, longitude=lon, method="nearest")
                .sel(time=slice(f"{year}-01-01", f"{year}-12-31T23:00"))
                .load()
            )
            frame = subset.to_dataframe().reset_index()
            frame["site_no"] = site_no
            frames.append(frame)
        finally:
            dataset.close()
    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("time").reset_index(drop=True)
    path = out_dir / f"{site_no}.parquet"
    result.to_parquet(path, index=False)
    return {
        "site_no": site_no,
        "rows": int(len(result)),
        "start": str(result["time"].min()),
        "end": str(result["time"].max()),
        "grid_latitude": float(result["latitude"].iloc[0]),
        "grid_longitude": float(result["longitude"].iloc[0]),
        "variables": variables,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "retrieved_at": utc_now(),
    }


def extract_aorc(
    cfg,
    *,
    years: list[int] | None = None,
    variables: list[str] | None = None,
    stations: str = "a_cohort",
    workers: int = 3,
    out_name: str = "aorc",
) -> dict:
    years = years or list(cfg.window.years)
    variables = variables or AORC_VARIABLES
    cohort = a_cohort(cfg) if stations == "a_cohort" else read_parquet(
        cfg.data_dir / "interim" / "candidates.parquet"
    )
    cohort["site_no"] = cohort["site_no"].astype(str)
    out_dir = ensure_dir(cfg.data_dir / "raw" / "forcing" / out_name)
    manifest = out_dir / "manifest.jsonl"
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _extract_aorc_station,
                row.site_no,
                float(row.lat),
                float(row.lon),
                years,
                variables,
                out_dir,
            ): row.site_no
            for row in cohort.itertuples()
        }
        for future in as_completed(futures):
            result = future.result()
            append_jsonl(manifest, result)
            results.append(result)
            print(
                f"[aorc] {result['site_no']} {result['start'][:10]}–{result['end'][:10]} "
                f"{result['rows']:,} rows {result['bytes'] / 1e6:.2f} MB",
                flush=True,
            )
    summary = {
        "source": "AORC v1.1",
        "store": AORC_BUCKET,
        "years": years,
        "variables": variables,
        "n_stations": len(results),
        "finished_at": utc_now(),
        "stations": results,
    }
    write_json(out_dir / "summary.json", summary)
    return summary


def extract_nldas(*args, **kwargs) -> dict:
    raise RuntimeError(
        "NLDAS-2 requires a NASA Earthdata login. Configure ~/.netrc with "
        "machine urs.earthdata.nasa.gov, then use the GES DISC subsetter or "
        "direct hourly files. See FORCING.md."
    )
