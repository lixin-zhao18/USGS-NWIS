"""Q-only event delineation and SC completeness screening."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from .io import ensure_dir, read_parquet, utc_now, write_json

EVENT_COLUMNS = [
    "site_no",
    "event_id",
    "start",
    "peak",
    "end",
    "peak_q",
    "base_q",
    "duration_h",
    "limb_points",
    "sc_completeness",
    "q_completeness",
    "usable",
]


def _hourly(series: pd.Series) -> pd.Series:
    return series.resample("1h").mean()


def _merge_overlaps(events: list[dict]) -> list[dict]:
    if not events:
        return []
    events = sorted(events, key=lambda item: item["start"])
    merged: list[dict] = [events[0].copy()]
    for event in events[1:]:
        current = merged[-1]
        if event["start"] <= current["end"]:
            if event["peak_q"] > current["peak_q"]:
                current["peak"] = event["peak"]
                current["peak_q"] = event["peak_q"]
                current["base_q"] = event["base_q"]
            current["end"] = max(current["end"], event["end"])
            current["start"] = min(current["start"], event["start"])
        else:
            merged.append(event.copy())
    return merged


def detect_events(q: pd.Series, cfg) -> pd.DataFrame:
    rules = cfg.events
    hourly = _hourly(q.dropna())
    hourly = hourly[(hourly >= 0) & hourly.notna()]
    if len(hourly) < rules.rolling_baseline_hours * 2:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    baseline = (
        hourly.rolling(
            f"{rules.rolling_baseline_hours}h",
            min_periods=rules.rolling_baseline_hours // 2,
        )
        .quantile(0.1)
        .shift(1)
    )
    positive = hourly[hourly > 0]
    floor = max(1e-6, float(positive.quantile(0.001)) * 0.01) if len(positive) else 1e-6
    logq = np.log10(hourly.clip(lower=floor).to_numpy())
    peaks, _ = find_peaks(
        logq,
        distance=rules.min_peak_distance_hours,
        prominence=rules.peak_prominence_log10,
    )

    candidates: list[dict] = []
    values = hourly.to_numpy()
    base_values = baseline.to_numpy()
    index = hourly.index
    for peak_index in peaks:
        peak_value = float(values[peak_index])
        base_value = float(base_values[peak_index])
        if not np.isfinite(base_value) or peak_value <= base_value:
            continue
        threshold = base_value + rules.recession_fraction * (peak_value - base_value)
        before = np.where(values[:peak_index] <= threshold)[0]
        if before.size == 0:
            continue
        start_index = int(before[-1])
        after = np.where(values[peak_index:] <= threshold)[0]
        end_index = peak_index + int(after[0]) if after.size else len(values) - 1
        candidates.append(
            {
                "start": index[start_index],
                "peak": index[peak_index],
                "end": index[min(end_index, len(values) - 1)],
                "peak_q": peak_value,
                "base_q": base_value,
                "limb_points": int(peak_index - start_index + 1),
            }
        )

    merged = _merge_overlaps(candidates)
    rows: list[dict] = []
    for event in merged:
        duration = (event["end"] - event["start"]).total_seconds() / 3600
        if duration < rules.min_event_hours or duration > rules.max_event_hours:
            continue
        # Hourly limb points are only a coarse floor; the native-resolution
        # count is applied in extract_site_events.
        if event["limb_points"] < min(2, rules.min_limb_points):
            continue
        event["duration_h"] = duration
        rows.append(event)
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _completeness(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    window = series[(series.index >= start) & (series.index <= end)]
    if window.empty:
        return 0.0
    return float(window.notna().mean())


def extract_site_events(frame: pd.DataFrame, site_no: str, cfg) -> pd.DataFrame:
    frame = frame.dropna(subset=["q_cfs"]).sort_values("time")
    if frame.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    q = frame.set_index("time")["q_cfs"]
    sc = frame.set_index("time")["sc_us_cm"]
    events = detect_events(q, cfg)
    if events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    # Count rising-limb points at the native cadence: a 2-hour rise has only
    # two hourly points but eight 15-minute points, and the latter is what a
    # hysteresis fit can use.
    native_q = q.dropna()
    events["limb_points"] = [
        int(native_q[(native_q.index >= row.start) & (native_q.index <= row.peak)].size)
        for row in events.itertuples()
    ]
    events = events[events["limb_points"] >= cfg.events.min_limb_points].reset_index(drop=True)
    if events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    q_hourly = _hourly(q)
    sc_hourly = _hourly(sc)
    events["site_no"] = site_no
    events["sc_completeness"] = [
        _completeness(sc_hourly, row.start, row.end) for row in events.itertuples()
    ]
    events["q_completeness"] = [
        _completeness(q_hourly, row.start, row.end) for row in events.itertuples()
    ]
    events["usable"] = (
        (events["sc_completeness"] >= cfg.events.min_event_sc_completeness)
        & (events["q_completeness"] >= cfg.events.min_event_q_completeness)
    )
    events = events.sort_values("start").reset_index(drop=True)
    events["event_id"] = [
        f"{site_no}_{row.start.year}_{index:03d}"
        for index, row in enumerate(events.itertuples(), start=1)
    ]
    return events[EVENT_COLUMNS]


def run_events(cfg) -> dict:
    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    eligible = candidates[candidates["candidate_ok"]].sort_values("site_no")
    station_dir = cfg.data_dir / "interim" / "stations"
    all_events: list[pd.DataFrame] = []
    summaries: list[dict] = []

    for site_no in eligible["site_no"].astype(str):
        path = station_dir / f"{site_no}.parquet"
        if not path.exists():
            continue
        frame = read_parquet(path)
        events = extract_site_events(frame, site_no, cfg)
        if not events.empty:
            all_events.append(events)
        summaries.append(
            {
                "site_no": site_no,
                "n_events": int(len(events)),
                "n_usable_events": int(events["usable"].sum()) if not events.empty else 0,
                "median_duration_h": (
                    float(events["duration_h"].median()) if not events.empty else None
                ),
                "median_sc_completeness": float(events["sc_completeness"].median())
                if not events.empty
                else None,
            }
        )

    processed = ensure_dir(cfg.data_dir / "processed")
    if all_events:
        combined = pd.concat(all_events, ignore_index=True)
    else:
        combined = pd.DataFrame(columns=EVENT_COLUMNS)
    combined.to_parquet(processed / "events.parquet", index=False)

    results_dir = ensure_dir(cfg.results_dir)
    pd.DataFrame(summaries).to_csv(results_dir / "event_station_summary.csv", index=False)
    write_json(
        results_dir / "events_run.json",
        {
            "finished_at": utc_now(),
            "n_stations": len(summaries),
            "n_events": int(len(combined)),
            "n_usable_events": int(combined["usable"].sum()) if not combined.empty else 0,
        },
    )
    return {
        "n_stations": len(summaries),
        "n_events": int(len(combined)),
        "n_usable_events": int(combined["usable"].sum()) if not combined.empty else 0,
    }
