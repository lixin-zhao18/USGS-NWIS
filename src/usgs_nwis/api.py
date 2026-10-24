"""Clients for the USGS legacy water services and ScienceBase.

The modern OGC API (api.waterdata.usgs.gov) is rate limited per IP (1000
requests per hour).  The legacy instantaneous-values service is still
supported until Q1 2027 and, importantly, accepts multi-site requests, which
keeps the number of HTTP calls low.  It returns local clock times with an
explicit UTC offset, so timestamps are converted unambiguously to UTC.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from .io import ensure_dir

USER_AGENT = "usgs-nwis-screening/0.1 (reproducible research)"


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchResult:
    sites: tuple[str, ...]
    param_codes: tuple[str, ...]
    start: str
    end: str
    url: str
    retrieved_at: str
    sha256: str
    n_rows: int
    n_series: int
    missing_sites: tuple[str, ...]
    frame: pd.DataFrame


def _site_number(location_id: str) -> str:
    return location_id.split("-", 1)[1] if location_id.startswith("USGS-") else location_id


def waterml_to_frame(payload: dict[str, Any]) -> pd.DataFrame:
    """Flatten a WaterML-JSON response into one row per observation."""
    series = payload.get("value", {}).get("timeSeries", [])
    frames: list[pd.DataFrame] = []
    for index, item in enumerate(series):
        try:
            site_no = item["sourceInfo"]["siteCode"][0]["value"]
            param = item["variable"]["variableCode"][0]["value"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiError(f"malformed timeSeries entry {index}") from exc
        name = item.get("name", "")
        method = ""
        if item.get("method"):
            method = str(item["method"][0].get("methodDescription") or "")
        values = item.get("values") or []
        observations = values[0].get("value", []) if values else []
        if not observations:
            continue
        frame = pd.DataFrame(
            {
                "site_no": site_no,
                "param_cd": param,
                "series_key": f"{name}|{method}",
                "series_name": name,
                "method": method,
                "time": [obs.get("dateTime") for obs in observations],
                "value": [obs.get("value") for obs in observations],
                "qualifiers": [
                    "".join(obs.get("qualifiers") or []) for obs in observations
                ],
            }
        )
        frames.append(frame)
    if not frames:
        return pd.DataFrame(
            columns=[
                "site_no",
                "param_cd",
                "series_key",
                "series_name",
                "method",
                "time",
                "value",
                "qualifiers",
            ]
        )
    frame = pd.concat(frames, ignore_index=True)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, format="ISO8601")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame


async def fetch_iv_batch(
    client: httpx.AsyncClient,
    iv_service: str,
    sites: Iterable[str],
    param_codes: Iterable[str],
    start: str,
    end: str,
    *,
    retries: int = 3,
) -> BatchResult:
    site_list = tuple(_site_number(s) for s in sites)
    param_list = tuple(param_codes)
    params = {
        "format": "json",
        "sites": ",".join(site_list),
        "startDT": start,
        "endDT": end,
        "parameterCd": ",".join(param_list),
        "siteStatus": "all",
    }
    request = client.build_request("GET", iv_service, params=params)
    url = str(request.url)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.send(request, follow_redirects=True)
            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", "60"))
                raise ApiError(f"429 rate limited; retry after {wait:.0f}s")
            response.raise_for_status()
            payload = response.json()
            frame = waterml_to_frame(payload)
            expected = set(site_list)
            present = set(frame["site_no"].unique()) if not frame.empty else set()
            return BatchResult(
                sites=site_list,
                param_codes=param_list,
                start=start,
                end=end,
                url=url,
                retrieved_at=pd.Timestamp.now(tz="UTC").isoformat(),
                sha256=hashlib.sha256(response.content).hexdigest(),
                n_rows=int(len(frame)),
                n_series=int(frame["series_key"].nunique()) if not frame.empty else 0,
                missing_sites=tuple(sorted(expected - present)),
                frame=frame,
            )
        except (httpx.HTTPError, ApiError, ValueError) as exc:
            last_error = exc
            if attempt == retries:
                break
            await asyncio.sleep(min(60.0, 5.0 * (3**attempt)))
    raise ApiError(f"failed to fetch IV batch {site_list}: {last_error}")


async def fetch_state_catalog(
    client: httpx.AsyncClient,
    site_service: str,
    state: str,
    param_codes: Iterable[str],
    *,
    retries: int = 3,
) -> tuple[str, bytes]:
    params = {
        "format": "rdb",
        "stateCd": state,
        "siteType": "ST",
        "siteStatus": "all",
        "seriesCatalogOutput": "true",
        "parameterCd": ",".join(param_codes),
        "hasDataTypeCd": "iv",
    }
    request = client.build_request("GET", site_service, params=params)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.send(request, follow_redirects=True)
            response.raise_for_status()
            text = response.text
            if "Error report" in text[:2000] or "<html" in text[:200].lower():
                raise ApiError(f"site service error for {state}")
            return state, response.content
        except (httpx.HTTPError, ApiError) as exc:
            last_error = exc
            if attempt == retries:
                break
            await asyncio.sleep(min(60.0, 5.0 * (3**attempt)))
    raise ApiError(f"failed to fetch site catalog for {state}: {last_error}")


def download_file(url: str, destination: str | Path, *, timeout_s: int = 600) -> int:
    destination = Path(destination)
    ensure_dir(destination.parent)
    with httpx.stream(
        "GET",
        url,
        timeout=timeout_s,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    return destination.stat().st_size
