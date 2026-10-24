"""Candidate catalog: USGS series inventory joined to GAGES-II attributes."""

from __future__ import annotations

import asyncio
import gzip
from pathlib import Path

import httpx
import pandas as pd

from .api import fetch_state_catalog
from .gages import prepare_gages2
from .io import (
    append_jsonl,
    ensure_dir,
    sha256_bytes,
    utc_now,
    write_json,
    write_parquet,
)
from .rdb import parse_rdb

_MIN_OVERLAP_YEARS = 3.0


def _read_state_cache(path: Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            return handle.read()
    return path.read_bytes()


async def fetch_catalogs(cfg, *, refresh: bool = False) -> list[dict]:
    raw_dir = ensure_dir(cfg.data_dir / "raw" / "catalog")
    manifest_path = raw_dir / "manifest.jsonl"
    params = sorted(cfg.params.values())
    records: list[dict] = []

    async def worker(client: httpx.AsyncClient, state: str) -> None:
        path = raw_dir / f"{state}.rdb.gz"
        if path.exists() and not refresh:
            payload = _read_state_cache(path)
            records.append(
                {
                    "state": state,
                    "cached": True,
                    "sha256": sha256_bytes(payload),
                    "bytes": len(payload),
                    "retrieved_at": None,
                }
            )
            return
        state_code, content = await fetch_state_catalog(
            client, cfg.data.site_service, state, params, retries=cfg.data.retries
        )
        with gzip.open(path, "wb") as handle:
            handle.write(content)
        record = {
            "state": state_code,
            "cached": False,
            "sha256": sha256_bytes(content),
            "bytes": len(content),
            "retrieved_at": utc_now(),
            "url": (
                f"{cfg.data.site_service}?format=rdb&stateCd={state}"
                f"&siteType=ST&siteStatus=all&seriesCatalogOutput=true"
                f"&parameterCd={','.join(params)}&hasDataTypeCd=iv"
            ),
        }
        append_jsonl(manifest_path, record)
        records.append(record)

    limits = httpx.Limits(max_connections=max(2, cfg.data.concurrency))
    async with httpx.AsyncClient(
        timeout=cfg.data.request_timeout_s,
        limits=limits,
        headers={"User-Agent": "usgs-nwis-screening/0.1"},
    ) as client:
        await asyncio.gather(*(worker(client, state) for state in cfg.data.states))
    return records


def parse_catalog(cfg) -> pd.DataFrame:
    raw_dir = cfg.data_dir / "raw" / "catalog"
    params = set(cfg.params.values())
    frames: list[pd.DataFrame] = []
    for state in cfg.data.states:
        path = raw_dir / f"{state}.rdb.gz"
        if not path.exists():
            continue
        frame = parse_rdb(_read_state_cache(path).decode("latin-1"))
        if frame.empty:
            continue
        frame = frame[frame["data_type_cd"] == "uv"].copy()
        frame = frame[frame["parm_cd"].isin(params)].copy()
        if frame.empty:
            continue
        frame["state"] = state
        frames.append(
            frame[
                [
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
                    "state",
                ]
            ]
        )
    if not frames:
        raise RuntimeError("no catalog rows parsed; run `catalog --refresh` first")
    catalog = pd.concat(frames, ignore_index=True)

    catalog["site_no"] = catalog["site_no"].astype(str).str.zfill(8)
    catalog["begin_date"] = pd.to_datetime(catalog["begin_date"], errors="coerce")
    catalog["end_date"] = pd.to_datetime(catalog["end_date"], errors="coerce")
    catalog["count_nu"] = pd.to_numeric(catalog["count_nu"], errors="coerce").fillna(0)
    for column in ("dec_lat_va", "dec_long_va"):
        catalog[column] = pd.to_numeric(catalog[column], errors="coerce")

    # One series per site and parameter: longest record, then latest end.
    catalog = catalog.sort_values(
        ["site_no", "parm_cd", "count_nu", "end_date"],
        ascending=[True, True, False, False],
    )
    catalog = catalog.drop_duplicates(["site_no", "parm_cd"], keep="first")

    wide = catalog.pivot(index="site_no", columns="parm_cd")
    wide.columns = [f"{left}_{right}" for left, right in wide.columns]
    wide = wide.reset_index()

    q, sc = cfg.params["streamflow"], cfg.params["specific_conductance"]
    required = [
        f"begin_date_{q}",
        f"end_date_{q}",
        f"begin_date_{sc}",
        f"end_date_{sc}",
        f"station_nm_{q}",
        f"dec_lat_va_{q}",
        f"dec_long_va_{q}",
        f"huc_cd_{q}",
        f"state_{q}",
    ]
    missing = [column for column in required if column not in wide.columns]
    if missing:
        raise RuntimeError(f"catalog missing expected columns: {missing}")

    sites = pd.DataFrame(
        {
            "site_no": wide["site_no"],
            "station_name": wide[f"station_nm_{q}"],
            "lat": wide[f"dec_lat_va_{q}"],
            "lon": wide[f"dec_long_va_{q}"],
            "huc": wide[f"huc_cd_{q}"],
            "state": wide[f"state_{q}"],
            "begin_q": wide[f"begin_date_{q}"],
            "end_q": wide[f"end_date_{q}"],
            "count_q": wide[f"count_nu_{q}"],
            "begin_sc": wide[f"begin_date_{sc}"],
            "end_sc": wide[f"end_date_{sc}"],
            "count_sc": wide[f"count_nu_{sc}"],
            "ts_id_q": wide[f"ts_id_{q}"].astype("Int64"),
            "ts_id_sc": wide[f"ts_id_{sc}"].astype("Int64"),
            "n_series_q": wide[f"count_nu_{q}"].notna().astype(int),
            "n_series_sc": wide[f"count_nu_{sc}"].notna().astype(int),
        }
    )
    sites = sites.dropna(subset=["begin_q", "end_q", "begin_sc", "end_sc"])
    sites["overlap_start"] = sites[["begin_q", "begin_sc"]].max(axis=1)
    sites["overlap_end"] = sites[["end_q", "end_sc"]].min(axis=1)
    sites["overlap_years"] = (
        (sites["overlap_end"] - sites["overlap_start"]).dt.total_seconds()
        / (365.25 * 86400)
    )
    sites["site_id"] = "USGS-" + sites["site_no"]
    return sites


def build_candidates(cfg, *, refresh_catalog: bool = False) -> pd.DataFrame:
    asyncio.run(fetch_catalogs(cfg, refresh=refresh_catalog))
    sites = parse_catalog(cfg)
    gages = prepare_gages2(cfg)

    merged = sites.merge(
        gages.drop(columns=["site_no"]), on="site_id", how="left"
    )

    window_end = pd.Timestamp(cfg.window.end).tz_localize(None)
    window_start = pd.Timestamp(cfg.window.start).tz_localize(None)
    candidates: list[dict] = []

    for row in merged.itertuples(index=False):
        reasons: list[str] = []
        if pd.isna(row.overlap_start) or row.overlap_years < _MIN_OVERLAP_YEARS:
            reasons.append("catalog_overlap_lt_3y")
        if pd.isna(row.overlap_end) or row.overlap_end < pd.Timestamp(
            year=cfg.window.end.year, month=1, day=1
        ):
            reasons.append("catalog_ends_before_window_end")
        if max(row.overlap_start, window_start) >= min(row.overlap_end, window_end):
            reasons.append("catalog_outside_window")
        if pd.isna(getattr(row, "DRAIN_SQKM", None)):
            reasons.append("gages2_missing")
        else:
            if not (cfg.candidate.area_min_km2 <= row.DRAIN_SQKM <= cfg.candidate.area_max_km2):
                reasons.append("area_outside_range")
            if row.DEVNLCD06 is not None and not pd.isna(row.DEVNLCD06):
                if row.DEVNLCD06 > cfg.candidate.max_dev_pct:
                    reasons.append("urban_dev_exceeds_limit")
            if row.DDENS_2009 is not None and not pd.isna(row.DDENS_2009):
                if row.DDENS_2009 > cfg.candidate.max_dam_density:
                    reasons.append("dam_density_exceeds_limit")

        name = str(getattr(row, "station_name", "") or "")
        upper = name.upper()
        for pattern in cfg.candidate.exclude_name_patterns:
            if pattern.upper() in upper:
                reasons.append(f"name_flag:{pattern}")
                break

        record = row._asdict()
        record["exclusion_reason"] = ";".join(reasons)
        candidates.append(record)

    frame = pd.DataFrame(candidates)
    frame["candidate_ok"] = frame["exclusion_reason"].eq("")
    frame["preferred_area"] = frame["DRAIN_SQKM"].between(
        cfg.candidate.preferred_area_min_km2,
        cfg.candidate.preferred_area_max_km2,
    )
    frame["low_disturbance"] = (frame["CLASS"] == "Ref") | (
        (frame["DEVNLCD06"] <= 10)
        & (frame["DDENS_2009"] <= 1)
        & (frame["MAJ_NDAMS_2009"] == 0)
    )
    frame["stratum"] = [
        _stratum(ai, snow, cfg)
        for ai, snow in zip(
            frame["aridity_index"], frame["SNOW_PCT_PRECIP"], strict=True
        )
    ]

    interim = ensure_dir(cfg.data_dir / "interim")
    write_parquet(frame, interim / "candidates.parquet")
    write_json(
        interim / "candidates_summary.json",
        {
            "catalog_sites_both_params": int(len(frame)),
            "candidate_ok": int(frame["candidate_ok"].sum()),
            "by_stratum": frame[frame["candidate_ok"]]["stratum"]
            .value_counts()
            .to_dict(),
            "by_state": frame[frame["candidate_ok"]]["state"].value_counts().to_dict(),
        },
    )
    return frame


def _stratum(aridity: float, snow_pct: float, cfg) -> str:
    if pd.notna(aridity) and aridity < cfg.strata.aridity_max:
        return "semiarid_flashy"
    if pd.notna(snow_pct) and snow_pct >= cfg.strata.snow_pct_min:
        return "snow_mixed"
    return "humid_rainfall"
