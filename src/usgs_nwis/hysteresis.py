"""Event-scale SC–Q hysteresis diagnostics on the discovery split.

Only Q and SC observations enter.  No catchment forcing, no model, and no
validation-split station is touched.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .io import ensure_dir, read_parquet, utc_now, write_json

EVENT_COLUMNS = [
    "site_no",
    "event_id",
    "start",
    "peak",
    "end",
    "duration_h",
    "peak_q",
    "base_q",
    "q_min",
    "q_max",
    "sc_min",
    "sc_max",
    "sc_median",
    "sc_range",
    "q_range",
    "n_grid",
    "valid_fraction",
    "n_rise",
    "n_fall",
    "rise_monotonic",
    "fall_monotonic",
    "sc_recovery",
    "sc_min_lag_fraction",
    "secondary_peak_ratio",
    "sc_shape_monotonic",
    "loop_area",
    "signed_area",
    "direction",
    "hi_mid",
    "q_mid",
    "sc_amplitude_ratio",
    "q_amplitude_ratio",
    "event_size",
    "month",
    "loop_ok",
    "skip_reason",
]

STATION_COLUMNS = [
    "site_no",
    "stratum",
    "n_usable_events",
    "n_loop_events",
    "loop_fraction",
    "n_clockwise",
    "n_counterclockwise",
    "n_ambiguous",
    "direction_fraction",
    "dominant_direction",
    "median_loop_area",
    "iqr_loop_area",
    "median_signed_area",
    "median_hi_mid",
    "median_sc_amplitude_ratio",
    "median_q_amplitude_ratio",
    "median_event_size",
    "first_half_median_area",
    "second_half_median_area",
    "split_half_direction_agreement",
    "split_half_area_ratio",
    "reproducible",
    "reproducibility_reason",
]


def _grid_cadence(times: pd.Series) -> int:
    diffs = times.sort_values().diff().dt.total_seconds().div(60)
    diffs = diffs[(diffs > 0) & (diffs <= 120)]
    if diffs.empty:
        return 15
    cadence = float(diffs.median())
    return int(min([5, 10, 15, 30, 60], key=lambda value: abs(value - cadence)))


def _interp_series(series: pd.Series, grid: pd.DatetimeIndex, limit: int) -> pd.Series:
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series.reindex(grid).interpolate(method="time", limit=limit, limit_area="inside")


def _limb_curve(q: np.ndarray, sc: np.ndarray, q_grid: np.ndarray) -> np.ndarray:
    order = np.argsort(q)
    q_sorted = q[order]
    sc_sorted = sc[order]
    frame = pd.DataFrame({"q": q_sorted, "sc": sc_sorted}).groupby("q", as_index=True).mean()
    return np.interp(
        q_grid,
        frame.index.to_numpy(),
        frame["sc"].to_numpy(),
        left=np.nan,
        right=np.nan,
    )


def event_metrics(frame: pd.DataFrame, event, cfg) -> dict:
    rules = cfg.hysteresis
    start = pd.Timestamp(event.start)
    end = pd.Timestamp(event.end)
    peak = pd.Timestamp(event.peak)
    subset = frame[(frame["time"] >= start) & (frame["time"] <= end)].copy()
    record = {column: None for column in EVENT_COLUMNS}
    record.update({
        "site_no": event.site_no,
        "event_id": event.event_id,
        "start": start,
        "peak": peak,
        "end": end,
        "duration_h": float(event.duration_h),
        "peak_q": float(event.peak_q),
        "base_q": float(event.base_q),
        "event_size": float(event.peak_q - event.base_q),
        "month": int(start.month),
        "loop_ok": False,
        "skip_reason": "",
    })
    if subset.empty:
        record["skip_reason"] = "no_native_points"
        return record

    cadence = _grid_cadence(subset["time"])
    first = subset["time"].min()
    last = subset["time"].max()
    grid = pd.date_range(first, last, freq=f"{cadence}min")
    q = _interp_series(subset.set_index("time")["q_cfs"], grid, rules.max_interp_gap_steps)
    sc = _interp_series(subset.set_index("time")["sc_us_cm"], grid, rules.max_interp_gap_steps)
    valid = q.notna() & sc.notna()
    record.update(
        {
            "n_grid": int(len(grid)),
            "valid_fraction": float(valid.mean()),
            "q_min": float(q.min()),
            "q_max": float(q.max()),
            "sc_min": float(sc.min()),
            "sc_max": float(sc.max()),
            "sc_median": float(sc.median()),
            "sc_range": float(sc.max() - sc.min()),
            "q_range": float(q.max() - q.min()),
        }
    )
    if valid.mean() < 0.80:
        record["skip_reason"] = "insufficient_valid_pairing"
        return record
    q_range = record["q_range"]
    sc_range = record["sc_range"]
    sc_median = abs(record["sc_median"]) if record["sc_median"] else np.nan
    if q_range <= 0:
        record["skip_reason"] = "zero_q_range"
        return record
    if sc_range < max(rules.min_sc_amplitude_us_cm, rules.min_sc_amplitude_fraction * sc_median):
        record["skip_reason"] = "sc_amplitude_too_small"
        return record

    peak_index = int(np.argmin(np.abs((grid - peak).total_seconds())))
    q_valid = q.to_numpy()
    sc_valid = sc.to_numpy()
    rising = slice(0, peak_index + 1)
    falling = slice(peak_index, len(grid))
    q_rise = q_valid[rising]
    sc_rise = sc_valid[rising]
    q_fall = q_valid[falling]
    sc_fall = sc_valid[falling]
    n_rise = int((np.isfinite(q_rise) & np.isfinite(sc_rise)).sum())
    n_fall = int((np.isfinite(q_fall) & np.isfinite(sc_fall)).sum())
    record["n_rise"] = n_rise
    record["n_fall"] = n_fall
    if n_rise < rules.min_limb_points or n_fall < rules.min_limb_points:
        record["skip_reason"] = "limb_too_short"
        return record
    if (np.nanmax(q_rise) - np.nanmin(q_rise)) < rules.min_q_range_fraction * q_range:
        record["skip_reason"] = "rising_limb_q_range_too_small"
        return record
    if (np.nanmax(q_fall) - np.nanmin(q_fall)) < rules.min_q_range_fraction * q_range:
        record["skip_reason"] = "falling_limb_q_range_too_small"
        return record

    tolerance = rules.reversal_tolerance_fraction * q_range

    def _monotonic(values: np.ndarray, sign: int) -> float:
        steps = np.diff(values)
        steps = steps[np.isfinite(steps)]
        if steps.size == 0:
            return 1.0
        ok = steps >= -tolerance if sign > 0 else steps <= tolerance
        return float(ok.mean())

    rise_monotonic = _monotonic(q_rise, 1)
    fall_monotonic = _monotonic(q_fall, -1)
    sc_finite = sc_valid[np.isfinite(sc_valid)]
    sc_start = float(sc_valid[np.isfinite(sc_valid)][0])
    sc_end = float(sc_valid[np.isfinite(sc_valid)][-1])
    sc_min = float(np.nanmin(sc_finite))
    denominator = sc_start - sc_min
    sc_recovery = 1.0 if denominator <= 0 else float((sc_end - sc_min) / denominator)
    valid_indices = np.where(np.isfinite(sc_valid))[0]
    min_index = int(valid_indices[np.argmin(sc_valid[valid_indices])])
    duration = max((end - start).total_seconds(), 1.0)
    sc_min_lag = float((grid[min_index] - peak).total_seconds() / duration)

    from scipy.signal import find_peaks

    q_finite = q_valid[np.isfinite(q_valid)]
    amplitude = float(np.nanmax(q_finite) - np.nanmin(q_finite))
    peak_indices, _ = find_peaks(q_finite, prominence=0.05 * amplitude)
    secondary_ratio = 0.0
    if peak_indices.size:
        main_index = int(np.argmax(q_finite))
        others = [
            float((q_finite[index] - np.nanmin(q_finite)) / amplitude)
            for index in peak_indices
            if abs(int(index) - main_index) > 2
        ]
        if others:
            secondary_ratio = max(others)

    # SC shape: require one dominant dilution (or flushing) excursion, not a
    # sequence of oscillations.
    sc_series = pd.Series(sc_valid).rolling(3, center=True, min_periods=1).median().to_numpy()
    sc_shape_tol = rules.sc_shape_tolerance_fraction * max(sc_range, 1e-9)
    valid_sc_idx = np.where(np.isfinite(sc_series))[0]
    sc_min_index = int(valid_sc_idx[np.argmin(sc_series[valid_sc_idx])])
    sc_max_index = int(valid_sc_idx[np.argmax(sc_series[valid_sc_idx])])
    min_excursion = abs(sc_series[valid_sc_idx[0]] - np.nanmin(sc_series))
    max_excursion = abs(np.nanmax(sc_series) - sc_series[valid_sc_idx[0]])
    if min_excursion >= max_excursion:
        extreme_index = sc_min_index
        before = sc_series[: extreme_index + 1]
        after = sc_series[extreme_index:]
        before_ok = np.diff(before) <= sc_shape_tol
        after_ok = np.diff(after) >= -sc_shape_tol
    else:
        extreme_index = sc_max_index
        before = sc_series[: extreme_index + 1]
        after = sc_series[extreme_index:]
        before_ok = np.diff(before) >= -sc_shape_tol
        after_ok = np.diff(after) <= sc_shape_tol
    before_ok = before_ok[np.isfinite(before_ok)]
    after_ok = after_ok[np.isfinite(after_ok)]
    sc_shape_monotonic = float(
        min(
            before_ok.mean() if before_ok.size else 1.0,
            after_ok.mean() if after_ok.size else 1.0,
        )
    )

    record.update(
        {
            "rise_monotonic": rise_monotonic,
            "fall_monotonic": fall_monotonic,
            "sc_recovery": sc_recovery,
            "sc_min_lag_fraction": sc_min_lag,
            "secondary_peak_ratio": secondary_ratio,
            "sc_shape_monotonic": sc_shape_monotonic,
        }
    )
    if rise_monotonic < rules.min_limb_monotonic_fraction:
        record["skip_reason"] = "rising_limb_not_monotonic"
        return record
    if fall_monotonic < rules.min_limb_monotonic_fraction:
        record["skip_reason"] = "falling_limb_not_monotonic"
        return record
    if sc_recovery < rules.min_sc_recovery:
        record["skip_reason"] = "sc_not_recovered"
        return record
    if not (rules.min_sc_min_lag_fraction <= sc_min_lag <= rules.max_sc_min_lag_fraction):
        record["skip_reason"] = "sc_minimum_misplaced"
        return record
    if secondary_ratio > rules.max_secondary_peak_ratio:
        record["skip_reason"] = "secondary_peak_too_large"
        return record
    if sc_shape_monotonic < rules.min_sc_shape_monotonic:
        record["skip_reason"] = "sc_shape_not_single_excursion"
        return record

    q_norm = (q_valid - np.nanmin(q_valid)) / q_range
    sc_norm = (sc_valid - np.nanmin(sc_valid)) / sc_range
    q_grid = np.linspace(
        max(np.nanmin(q_norm), 0.02),
        min(np.nanmax(q_norm), 0.98),
        rules.grid_points,
    )
    rise_curve = _limb_curve(q_norm[rising], sc_norm[rising], q_grid)
    fall_curve = _limb_curve(q_norm[falling], sc_norm[falling], q_grid)
    both = np.isfinite(rise_curve) & np.isfinite(fall_curve)
    if both.sum() < 10:
        record["skip_reason"] = "curves_do_not_overlap"
        return record
    diff = fall_curve - rise_curve
    signed_area = float(np.trapezoid(diff[both], q_grid[both]))
    loop_area = float(np.trapezoid(np.abs(diff[both]), q_grid[both]))
    q_mid = float(np.median(q_grid[both]))
    hi_mid = float(np.interp(q_mid, q_grid[both], diff[both]))
    direction = "ambiguous"
    if signed_area > rules.ambiguous_area_threshold:
        direction = "counterclockwise"
    elif signed_area < -rules.ambiguous_area_threshold:
        direction = "clockwise"

    record.update(
        {
            "loop_area": loop_area,
            "signed_area": signed_area,
            "direction": direction,
            "hi_mid": hi_mid,
            "q_mid": q_mid,
            "sc_amplitude_ratio": sc_range / sc_median if sc_median else np.nan,
            "q_amplitude_ratio": q_range / record["base_q"] if record["base_q"] > 0 else np.nan,
            "loop_ok": True,
            "skip_reason": "",
        }
    )
    return record


def _dominant(directions: pd.Series) -> tuple[str, float]:
    usable = directions[directions != "ambiguous"]
    if usable.empty:
        return "ambiguous", 0.0
    counts = usable.value_counts()
    return str(counts.index[0]), float(counts.iloc[0] / len(usable))


def station_summary(events: pd.DataFrame, cfg, stratum: str) -> dict:
    loop = events[events["loop_ok"]].sort_values("start").reset_index(drop=True)
    record = {
        "site_no": events["site_no"].iloc[0] if not events.empty else None,
        "stratum": stratum,
        "n_usable_events": int(len(events)),
        "n_loop_events": int(len(loop)),
        "loop_fraction": float(len(loop) / len(events)) if len(events) else 0.0,
        "n_clockwise": int((loop["direction"] == "clockwise").sum()),
        "n_counterclockwise": int((loop["direction"] == "counterclockwise").sum()),
        "n_ambiguous": int((loop["direction"] == "ambiguous").sum()),
        "median_loop_area": float(loop["loop_area"].median()) if len(loop) else None,
        "iqr_loop_area": float(loop["loop_area"].quantile(0.75) - loop["loop_area"].quantile(0.25))
        if len(loop)
        else None,
        "median_signed_area": float(loop["signed_area"].median()) if len(loop) else None,
        "median_hi_mid": float(loop["hi_mid"].median()) if len(loop) else None,
        "median_sc_amplitude_ratio": (
            float(loop["sc_amplitude_ratio"].median()) if len(loop) else None
        ),
        "median_q_amplitude_ratio": (
            float(loop["q_amplitude_ratio"].median()) if len(loop) else None
        ),
        "median_event_size": float(loop["event_size"].median()) if len(loop) else None,
    }
    dominant, direction_fraction = _dominant(loop["direction"]) if len(loop) else ("ambiguous", 0.0)
    record["dominant_direction"] = dominant
    record["direction_fraction"] = direction_fraction

    half = len(loop) // 2
    reasons: list[str] = []
    if half >= 2 and len(loop) - half >= 2:
        first = loop.iloc[:half]
        second = loop.iloc[half:]
        first_dominant, _ = _dominant(first["direction"])
        second_usable = second[second["direction"] != "ambiguous"]
        agreement = (
            float((second_usable["direction"] == first_dominant).mean())
            if not second_usable.empty and first_dominant != "ambiguous"
            else np.nan
        )
        first_area = float(first["loop_area"].median())
        second_area = float(second["loop_area"].median())
        record["first_half_median_area"] = first_area
        record["second_half_median_area"] = second_area
        area_ratio = (
            float(min(first_area, second_area) / max(first_area, second_area))
            if max(first_area, second_area) > 0
            else np.nan
        )
    else:
        agreement = np.nan
        area_ratio = np.nan
        record["first_half_median_area"] = None
        record["second_half_median_area"] = None
    record["split_half_direction_agreement"] = agreement
    record["split_half_area_ratio"] = area_ratio

    rules = cfg.hysteresis
    if len(loop) < rules.min_loop_events:
        reasons.append("too_few_loop_events")
    if direction_fraction < rules.min_direction_fraction:
        reasons.append("direction_inconsistent")
    if (record["median_loop_area"] or 0.0) < rules.min_median_area:
        reasons.append("loop_area_too_small")
    if not np.isfinite(agreement) or agreement < rules.min_direction_fraction:
        reasons.append("split_half_direction_unstable")
    record["reproducible"] = not reasons
    record["reproducibility_reason"] = ";".join(reasons)
    return record


def run_hysteresis(cfg) -> dict:
    selection = pd.read_csv(cfg.experiment_dir / "selection.csv", dtype={"site_no": str})
    discovery = selection[selection["split"] == "discovery"].copy()
    events = pd.read_parquet(cfg.data_dir / "processed" / "events.parquet")
    events["site_no"] = events["site_no"].astype(str)
    events = events[events["site_no"].isin(discovery["site_no"]) & events["usable"]].copy()
    if events.empty:
        raise RuntimeError("no usable discovery events; run `events` and `select` first")

    station_dir = cfg.data_dir / "interim" / "stations"
    event_rows: list[dict] = []
    station_rows: list[dict] = []
    for site_no, group in events.groupby("site_no", sort=True):
        frame = read_parquet(station_dir / f"{site_no}.parquet")
        rows = [event_metrics(frame, event, cfg) for event in group.itertuples()]
        site_events = pd.DataFrame(rows)
        stratum = discovery.loc[discovery["site_no"] == site_no, "stratum"].iloc[0]
        event_rows.extend(rows)
        station_rows.append(station_summary(site_events, cfg, stratum))

    event_frame = pd.DataFrame(event_rows)
    station_frame = pd.DataFrame(station_rows)
    results_dir = ensure_dir(cfg.results_dir)
    event_frame.to_csv(results_dir / "discovery_event_metrics.csv", index=False)
    station_frame[STATION_COLUMNS].to_csv(
        results_dir / "discovery_station_summary.csv", index=False
    )

    ok_stations = station_frame[station_frame["reproducible"]]
    summary = {
        "finished_at": utc_now(),
        "n_discovery_stations": int(len(discovery)),
        "n_stations_with_events": int(len(station_frame)),
        "n_events_evaluated": int(len(event_frame)),
        "n_loop_events": int(event_frame["loop_ok"].sum()),
        "n_reproducible_stations": int(len(ok_stations)),
        "reproducible_fraction": float(len(ok_stations) / len(station_frame))
        if len(station_frame)
        else 0.0,
        "data_sufficient": bool(
            len(station_frame) > 0
            and (len(ok_stations) / len(station_frame)) >= cfg.hysteresis.min_station_pass_fraction
        ),
        "direction_counts": event_frame.loc[event_frame["loop_ok"], "direction"]
        .value_counts()
        .to_dict(),
        "criteria": {
            "min_loop_events": cfg.hysteresis.min_loop_events,
            "min_direction_fraction": cfg.hysteresis.min_direction_fraction,
            "min_median_area": cfg.hysteresis.min_median_area,
            "min_station_pass_fraction": cfg.hysteresis.min_station_pass_fraction,
        },
    }
    write_json(results_dir / "hysteresis_summary.json", summary)
    from .report import write_hysteresis_report

    write_hysteresis_report(cfg)
    return summary


def event_trajectory(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cfg,
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    """Return the normalized Q and SC trajectory of one event (for figures)."""
    subset = frame[(frame["time"] >= start) & (frame["time"] <= end)].copy()
    if subset.empty:
        raise ValueError("event outside station record")
    cadence = _grid_cadence(subset["time"])
    grid = pd.date_range(subset["time"].min(), subset["time"].max(), freq=f"{cadence}min")
    q = _interp_series(
        subset.set_index("time")["q_cfs"], grid, cfg.hysteresis.max_interp_gap_steps
    )
    sc = _interp_series(
        subset.set_index("time")["sc_us_cm"], grid, cfg.hysteresis.max_interp_gap_steps
    )
    qn = (q - q.min()) / (q.max() - q.min())
    sn = (sc - sc.min()) / (sc.max() - sc.min())
    return grid, qn.to_numpy(), sn.to_numpy()
