"""Batched download of instantaneous Q and SC records."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pandas as pd

from .api import ApiError, fetch_iv_batch
from .io import (
    append_jsonl,
    ensure_dir,
    read_parquet,
    utc_now,
    write_json,
    write_parquet,
)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _choose_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Pick one time series per site and parameter.

    The legacy service can return several methods for the same parameter.  The
    series with the most approved observations over the whole window is used;
    the choice is recorded in the site metadata.
    """
    if frame.empty:
        return frame
    valid = frame.dropna(subset=["value"])
    if valid.empty:
        return frame.iloc[0:0]
    counts = (
        valid.groupby(["site_no", "param_cd", "series_key"], sort=True)
        .size()
        .reset_index(name="n")
        .sort_values(
            ["site_no", "param_cd", "n", "series_key"],
            ascending=[True, True, False, True],
        )
        .drop_duplicates(["site_no", "param_cd"], keep="first")
    )
    chosen = counts[["site_no", "param_cd", "series_key"]]
    return frame.merge(chosen, on=["site_no", "param_cd", "series_key"], how="inner")


def _site_frame(
    frame: pd.DataFrame,
    site_no: str,
    param_q: str,
    param_sc: str,
    approved_only: bool,
) -> tuple[pd.DataFrame, dict]:
    meta: dict = {"series": {}}
    if frame.empty or "site_no" not in frame.columns:
        return (
            pd.DataFrame(columns=["time", "q_cfs", "sc_us_cm", "q_qual", "sc_qual"]),
            meta,
        )
    if approved_only:
        frame = frame[
            frame["qualifiers"].fillna("").str.contains("A", regex=False)
        ]
    selected = _choose_series(frame[frame["site_no"] == site_no])
    columns: dict[str, pd.DataFrame] = {}
    for param, column in ((param_q, "q_cfs"), (param_sc, "sc_us_cm")):
        part = selected[selected["param_cd"] == param].copy()
        if part.empty:
            continue
        if approved_only:
            part = part[part["qualifiers"].fillna("").str.contains("A", regex=False)]
        part = (
            part.dropna(subset=["time", "value"])
            .sort_values("time")
            .drop_duplicates("time", keep="last")
        )
        if part.empty:
            continue
        meta["series"][param] = {
            "series_name": str(part["series_name"].iloc[0]),
            "method": str(part["method"].iloc[0]),
            "n_values": int(len(part)),
            "start": part["time"].min().isoformat(),
            "end": part["time"].max().isoformat(),
            "series_key": str(part["series_key"].iloc[0]),
        }
        columns[column] = part.set_index("time")["value"].rename(column)
        columns[column.replace("_cfs", "_qual").replace("_us_cm", "_qual")] = part.set_index(
            "time"
        )["qualifiers"].rename(
            "q_qual" if column == "q_cfs" else "sc_qual"
        )

    if not columns:
        return pd.DataFrame(columns=["time", "q_cfs", "sc_us_cm", "q_qual", "sc_qual"]), meta
    wide = pd.concat(columns.values(), axis=1).sort_index()
    wide = wide.reset_index().rename(columns={"index": "time"})
    for column in ("q_cfs", "sc_us_cm", "q_qual", "sc_qual"):
        if column not in wide.columns:
            wide[column] = pd.NA
    return wide[["time", "q_cfs", "sc_us_cm", "q_qual", "sc_qual"]], meta


async def _download_batch(
    client: httpx.AsyncClient,
    cfg,
    sites: list[str],
    semaphore: asyncio.Semaphore,
    raw_manifest: Path,
    errors_path: Path,
    progress: dict,
) -> None:
    async with semaphore:
        frames: list[pd.DataFrame] = []
        for year in cfg.window.years:
            start = f"{year}-01-01"
            end = f"{year}-12-31"
            try:
                result = await fetch_iv_batch(
                    client,
                    cfg.data.iv_service,
                    sites,
                    [cfg.params["streamflow"], cfg.params["specific_conductance"]],
                    start,
                    end,
                    retries=cfg.data.retries,
                )
            except ApiError as exc:
                append_jsonl(
                    errors_path,
                    {"stage": "download", "sites": sites, "year": year, "error": str(exc)},
                )
                continue
            append_jsonl(
                raw_manifest,
                {
                    "stage": "download",
                    "sites": list(result.sites),
                    "start": result.start,
                    "end": result.end,
                    "url": result.url,
                    "retrieved_at": result.retrieved_at,
                    "sha256": result.sha256,
                    "n_rows": result.n_rows,
                    "n_series": result.n_series,
                    "missing_sites": list(result.missing_sites),
                },
            )
            if not result.frame.empty:
                frames.append(result.frame)
            progress["requests"] += 1
            progress["rows"] += result.n_rows

        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        station_dir = ensure_dir(cfg.data_dir / "interim" / "stations")
        for site_no in sites:
            frame, meta = _site_frame(
                combined,
                site_no,
                cfg.params["streamflow"],
                cfg.params["specific_conductance"],
                cfg.quality.approved_only_primary,
            )
            write_parquet(frame, station_dir / f"{site_no}.parquet")
            meta.update(
                {
                    "site_no": site_no,
                    "window_start": cfg.window.start_utc,
                    "window_end": cfg.window.end_utc,
                    "written_at": utc_now(),
                    "n_rows": int(len(frame)),
                }
            )
            write_json(station_dir / f"{site_no}.json", meta)
        progress["batches"] += 1


async def _download_all_async(
    cfg, *, refresh: bool = False, site_limit: int | None = None
) -> dict:
    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    selected = candidates[candidates["candidate_ok"]].copy()
    selected = selected.sort_values("site_no")
    if site_limit is not None:
        selected = selected.head(site_limit)
    sites = selected["site_no"].astype(str).tolist()

    station_dir = ensure_dir(cfg.data_dir / "interim" / "stations")
    if not refresh:
        sites = [s for s in sites if not (station_dir / f"{s}.parquet").exists()]

    raw_dir = ensure_dir(cfg.data_dir / "raw" / "iv")
    raw_manifest = raw_dir / "manifest.jsonl"
    errors_path = ensure_dir(cfg.data_dir / "interim") / "errors.jsonl"
    progress = {"requests": 0, "rows": 0, "batches": 0}

    batches = _chunks(sites, cfg.data.batch_sites)
    semaphore = asyncio.Semaphore(max(1, cfg.data.concurrency))
    limits = httpx.Limits(max_connections=max(2, cfg.data.concurrency))
    async with httpx.AsyncClient(
        timeout=cfg.data.request_timeout_s,
        limits=limits,
        headers={"User-Agent": "usgs-nwis-screening/0.1"},
    ) as client:
        tasks = [
            _download_batch(client, cfg, batch, semaphore, raw_manifest, errors_path, progress)
            for batch in batches
        ]
        for index, task in enumerate(asyncio.as_completed(tasks), start=1):
            await task
            print(
                f"[download] batch {index}/{len(tasks)} | "
                f"sites {progress['batches'] * cfg.data.batch_sites}"
                f" | requests {progress['requests']} | rows {progress['rows']:,}",
                flush=True,
            )

    return {
        "sites_requested": len(sites),
        "batches": len(batches),
        "requests": progress["requests"],
        "rows": progress["rows"],
        "finished_at": utc_now(),
    }


def download_all(cfg, *, refresh: bool = False, site_limit: int | None = None) -> dict:
    """Synchronous wrapper used by the CLI and pipeline."""
    return asyncio.run(
        _download_all_async(cfg, refresh=refresh, site_limit=site_limit)
    )
