"""GAGES-II basin attributes used for area, climate and disturbance."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

from .io import ensure_dir, sha256_file, write_json

# Columns kept from the GAGES-II spreadsheets.
_BASINID = ["STAID", "STANAME", "DRAIN_SQKM", "HUC02", "LAT_GAGE", "LNG_GAGE", "STATE"]
_CLIMATE = ["STAID", "PPTAVG_BASIN", "T_AVG_BASIN", "PET", "SNOW_PCT_PRECIP"]
_DAMS = [
    "STAID",
    "NDAMS_2009",
    "DDENS_2009",
    "MAJ_NDAMS_2009",
    "STOR_NID_2009",
]
_CLASS = ["STAID", "CLASS", "AGGECOREGION", "HYDRO_DISTURB_INDX"]
_LANDCOVER = ["STAID", "DEVNLCD06", "CROPSNLCD06", "FORESTNLCD06"]


def _read_member(archive: zipfile.ZipFile, name: str, columns: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(
        io.BytesIO(archive.read(name)),
        dtype={"STAID": str},
        encoding="latin-1",
        low_memory=False,
    )
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")
    return frame[columns].copy()


def build_attributes(zip_path: str | Path) -> pd.DataFrame:
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as outer:
        inner = zipfile.ZipFile(io.BytesIO(outer.read("spreadsheets-in-csv-format.zip")))
        basin = _read_member(inner, "conterm_basinid.txt", _BASINID)
        climate = _read_member(inner, "conterm_climate.txt", _CLIMATE)
        dams = _read_member(inner, "conterm_hydromod_dams.txt", _DAMS)
        classes = _read_member(inner, "conterm_bas_classif.txt", _CLASS)
        land = _read_member(inner, "conterm_lc06_basin.txt", _LANDCOVER)

    frame = (
        basin.merge(climate, on="STAID", how="left")
        .merge(dams, on="STAID", how="left")
        .merge(classes, on="STAID", how="left")
        .merge(land, on="STAID", how="left")
    )
    frame["site_no"] = frame["STAID"].str.zfill(8)
    frame["site_id"] = "USGS-" + frame["site_no"]
    frame["aridity_index"] = frame["PPTAVG_BASIN"] * 10.0 / frame["PET"]
    return frame


def prepare_gages2(cfg, *, refresh: bool = False) -> pd.DataFrame:
    raw_dir = ensure_dir(cfg.data_dir / "raw" / "gages2")
    zip_path = raw_dir / "basinchar_and_report_sept_2011.zip"
    if refresh or not zip_path.exists():
        import httpx

        with httpx.stream(
            "GET", cfg.data.gages2_file, timeout=900, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with zip_path.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
    attributes = build_attributes(zip_path)
    out = ensure_dir(cfg.data_dir / "interim") / "gages2_attributes.parquet"
    attributes.to_parquet(out, index=False)
    write_json(
        raw_dir / "manifest.json",
        {
            "file": zip_path.name,
            "bytes": zip_path.stat().st_size,
            "sha256": sha256_file(zip_path),
            "n_gages": int(len(attributes)),
            "columns": sorted(attributes.columns),
        },
    )
    return attributes
