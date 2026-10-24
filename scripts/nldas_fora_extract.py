"""Download NLDAS-2 FORA hourly files and extract the 29 A-cohort points.

The raw files are one hour each and cover the whole 0.125 degree grid.
They are downloaded to a temporary directory, reduced to the nearest grid
cell for each station, and deleted immediately.

Example:
    python scripts/nldas_fora_extract.py --start 2015-01-01 --end 2015-01-01
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import earthaccess
import numpy as np
import pandas as pd
import xarray as xr

SHORT_NAME = "NLDAS_FORA0125_H"
VERSION = "2.0"
VARIABLES = [
    "Tair",
    "Qair",
    "PSurf",
    "Wind_E",
    "Wind_N",
    "LWdown",
    "SWdown",
    "Rainf",
    "CRainf_frac",
    "CAPE",
    "PotEvap",
]


def load_stations(path: Path) -> pd.DataFrame:
    stations = pd.read_csv(path, dtype={"site_no": str})
    stations = stations[stations["in_a_cohort"]].copy()
    if stations.empty:
        raise SystemExit("no A-cohort stations in the station table")
    return stations[["site_no", "latitude_nwis", "longitude_nwis"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="First date (inclusive), YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="Last date (inclusive), YYYY-MM-DD.")
    parser.add_argument(
        "--stations",
        type=Path,
        default=Path("../usgs-qsc-hysteresis-dataset/data/stations.csv"),
    )
    parser.add_argument(
        "--out", type=Path, default=Path("data/raw/forcing/nldas_fora")
    )
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    out_dir = args.out / f"{args.start}_{args.end}"
    if (out_dir / "all_stations.parquet").exists() and not args.overwrite:
        print(f"already complete: {out_dir}; use --overwrite to rerun")
        return

    stations = load_stations(args.stations)
    earthaccess.login(strategy="netrc")
    granules = earthaccess.search_data(
        short_name=SHORT_NAME,
        version=VERSION,
        temporal=(args.start, f"{args.end}T23:59:59"),
        count=-1,
    )
    print(f"granules: {len(granules)}")
    if not granules:
        raise SystemExit("no granules returned")

    tmp = Path(".cache") / "nldas_download"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    files = earthaccess.download(granules, str(tmp), threads=args.threads)
    print(f"downloaded: {len(files)} files")

    lat_ref = None
    lon_ref = None
    lat_idx = None
    lon_idx = None
    rows: dict[str, list[dict]] = {site: [] for site in stations["site_no"]}
    for path in files:
        try:
            ds = xr.open_dataset(path)
        except Exception as exc:  # noqa: BLE001
            print("skip unreadable", path, exc)
            continue
        if lat_ref is None:
            lat_ref = ds["lat"].values
            lon_ref = ds["lon"].values
            lat_idx = np.array(
                [int(np.abs(lat_ref - lat).argmin()) for lat in stations["latitude_nwis"]]
            )
            lon_idx = np.array(
                [int(np.abs(lon_ref - lon).argmin()) for lon in stations["longitude_nwis"]]
            )
        time = pd.Timestamp(ds["time"].values[0]).tz_localize("UTC")
        values = {
            var: ds[var].values[0][lat_idx, lon_idx].astype(float) for var in VARIABLES
        }
        for k, site in enumerate(stations["site_no"]):
            row = {"time": time, "site_no": site}
            row.update({var: values[var][k] for var in VARIABLES})
            rows[site].append(row)
        ds.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for site, site_rows in rows.items():
        frame = pd.DataFrame(site_rows)
        if frame.empty:
            continue
        frame["site_no"] = site
        frames.append(frame)
        frame.to_parquet(out_dir / f"{site}.parquet", index=False)
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined.to_parquet(out_dir / "all_stations.parquet", index=False)
        print(
            f"rows={len(combined):,} stations={combined['site_no'].nunique()} "
            f"time={combined['time'].min()}..{combined['time'].max()}"
        )
    if not args.keep_raw:
        shutil.rmtree(tmp, ignore_errors=True)
        print("raw files removed")
    else:
        print("raw files kept at", tmp)


if __name__ == "__main__":
    main()
