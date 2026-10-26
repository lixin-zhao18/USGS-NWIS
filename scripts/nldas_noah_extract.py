"""Download NLDAS-2 Noah hourly files and extract the 29 A-cohort points.

Companion to ``nldas_fora_extract.py``. FORA provides the meteorological
forcing; Noah provides the land-surface state (soil moisture in four layers,
snow water equivalent) on the same 0.125 degree grid and the same hourly
time axis, so the two line up cell-for-cell.

Raw files are downloaded to a temporary directory, reduced to the nearest
grid cell for each station, and deleted immediately.

Units (netCDF attributes): soil moisture and SWE are ``kg m-2``, which for
water is numerically mm of water. Volumetric soil moisture is
``SoilM / layer_thickness_mm`` (0-10: 100 mm, 10-40: 300 mm,
40-100: 600 mm, 100-200: 1000 mm).

Example:
    python scripts/nldas_noah_extract.py --start 2015-01-01 --end 2015-01-01
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import earthaccess
import numpy as np
import pandas as pd
import xarray as xr

SHORT_NAME = "NLDAS_NOAH0125_H"
VERSION = "2.0"
VARIABLES = [
    "SoilM_0_10cm",
    "SoilM_10_40cm",
    "SoilM_40_100cm",
    "SoilM_100_200cm",
    "SWE",
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
        "--out", type=Path, default=Path("data/raw/forcing/nldas_noah")
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

    tmp = Path(".cache") / "nldas_noah_download"
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
    missing_reported = False
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
            # Noah has a land mask: a coastal station's nearest cell can be
            # open water with all-NaN soil moisture. Move such stations to the
            # nearest cell that has data, once, and keep it for every file.
            probe = next(v for v in VARIABLES if v in ds.variables)
            field = ds[probe].values[0]
            for k in range(len(stations)):
                if not np.isnan(field[lat_idx[k], lon_idx[k]]):
                    continue
                best = None
                for di in range(-3, 4):
                    for dj in range(-3, 4):
                        ii, jj = lat_idx[k] + di, lon_idx[k] + dj
                        if not (0 <= ii < field.shape[0] and 0 <= jj < field.shape[1]):
                            continue
                        if np.isnan(field[ii, jj]):
                            continue
                        dist = di * di + dj * dj
                        if best is None or dist < best[0]:
                            best = (dist, ii, jj)
                if best is not None:
                    print(
                        f"land-mask fix {stations['site_no'].iloc[k]}: "
                        f"cell ({lat_idx[k]},{lon_idx[k]}) -> ({best[1]},{best[2]})"
                    )
                    lat_idx[k], lon_idx[k] = best[1], best[2]
                else:
                    print(f"WARNING no land cell within 3 cells for {stations['site_no'].iloc[k]}")
            missing = [v for v in VARIABLES if v not in ds.variables]
            if missing and not missing_reported:
                print(f"WARNING missing variables {missing}; available: {sorted(ds.variables)}")
                missing_reported = True
        time = pd.Timestamp(ds["time"].values[0]).tz_localize("UTC")
        values = {
            var: ds[var].values[0][lat_idx, lon_idx].astype(float)
            for var in VARIABLES
            if var in ds.variables
        }
        for k, site in enumerate(stations["site_no"]):
            row = {"time": time, "site_no": site}
            row.update({var: values[var][k] for var in values})
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
