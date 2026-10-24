"""Per-year cadence and paired-completeness assessment."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from .io import ensure_dir, read_parquet, utc_now, write_json


def _year_start(year: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(year, 1, 1, tzinfo=UTC))


def _year_seconds(year: int) -> int:
    return int((_year_start(year + 1) - _year_start(year)).total_seconds())


def cadence_minutes(times: pd.Series) -> float | None:
    diffs = times.sort_values().diff().dt.total_seconds().div(60)
    diffs = diffs[(diffs > 0) & (diffs < 1440)]
    if diffs.empty:
        return None
    return float(diffs.median())


def _grid_completeness(
    times: pd.Series,
    start: pd.Timestamp,
    resolution_s: float,
    expected: int,
) -> float:
    if times.empty or expected <= 0:
        return 0.0
    offsets = (times - start).dt.total_seconds().to_numpy() / resolution_s
    indices = np.rint(offsets).astype(np.int64)
    indices = indices[(indices >= 0) & (indices < expected)]
    if indices.size == 0:
        return 0.0
    return float(np.unique(indices).size / expected)


def assess_year(
    q: pd.DataFrame,
    sc: pd.DataFrame,
    year: int,
    max_cadence: float,
    min_paired: float,
) -> dict:
    start = _year_start(year)
    end = _year_start(year + 1)
    qy = q[(q["time"] >= start) & (q["time"] < end)].dropna(subset=["q_cfs"])
    sy = sc[(sc["time"] >= start) & (sc["time"] < end)].dropna(subset=["sc_us_cm"])
    result = {
        "year": year,
        "n_q": int(len(qy)),
        "n_sc": int(len(sy)),
        "cadence_q_min": None,
        "cadence_sc_min": None,
        "q_completeness": None,
        "sc_completeness": None,
        "paired_completeness": None,
        "qualifying": False,
        "reason": "",
    }
    if len(qy) < 10 or len(sy) < 10:
        result["reason"] = "insufficient_points"
        return result

    cad_q = cadence_minutes(qy["time"])
    cad_sc = cadence_minutes(sy["time"])
    result["cadence_q_min"] = cad_q
    result["cadence_sc_min"] = cad_sc
    if cad_q is None or cad_sc is None:
        result["reason"] = "cadence_undefined"
        return result
    if cad_q > max_cadence or cad_sc > max_cadence:
        result["reason"] = "cadence_too_coarse"
        return result

    resolution_min = max(cad_q, cad_sc)
    resolution_s = resolution_min * 60.0
    expected = int(round(_year_seconds(year) / resolution_s))
    result["q_completeness"] = _grid_completeness(qy["time"], start, resolution_s, expected)
    result["sc_completeness"] = _grid_completeness(sy["time"], start, resolution_s, expected)

    q_idx = np.rint(
        (qy["time"] - start).dt.total_seconds().to_numpy() / resolution_s
    ).astype(np.int64)
    s_idx = np.rint(
        (sy["time"] - start).dt.total_seconds().to_numpy() / resolution_s
    ).astype(np.int64)
    q_set = np.unique(q_idx[(q_idx >= 0) & (q_idx < expected)])
    s_set = np.unique(s_idx[(s_idx >= 0) & (s_idx < expected)])
    paired = np.intersect1d(q_set, s_set, assume_unique=True)
    paired_completeness = float(paired.size / expected) if expected else 0.0
    result["paired_completeness"] = paired_completeness
    result["qualifying"] = paired_completeness >= min_paired
    if not result["qualifying"]:
        result["reason"] = "paired_completeness_below_threshold"
    return result


def assess_station(frame: pd.DataFrame, cfg) -> tuple[list[dict], dict]:
    q = frame[["time", "q_cfs"]].dropna(subset=["q_cfs"])
    sc = frame[["time", "sc_us_cm"]].dropna(subset=["sc_us_cm"])
    rows = [
        assess_year(
            q,
            sc,
            year,
            cfg.quality.max_cadence_minutes,
            cfg.quality.min_paired_completeness,
        )
        for year in cfg.window.years
    ]

    qualifying = [row for row in rows if row["qualifying"]]
    paired_values = [row["paired_completeness"] for row in qualifying]
    cad_q_values = [row["cadence_q_min"] for row in rows if row["cadence_q_min"]]
    cad_sc_values = [row["cadence_sc_min"] for row in rows if row["cadence_sc_min"]]
    sc_values = sc["sc_us_cm"]
    q_values = q["q_cfs"]

    sc_median = float(sc_values.median()) if len(sc_values) else None
    negative_q = float((q_values < 0).mean()) if len(q_values) else 1.0
    negative_sc = float((sc_values < 0).mean()) if len(sc_values) else 1.0
    anomalies: list[str] = []
    if sc_median is None:
        anomalies.append("sc_missing")
    elif not (
        cfg.quality.min_sc_median_us_cm <= sc_median <= cfg.quality.max_sc_median_us_cm
    ):
        anomalies.append("sc_median_out_of_range")
    if negative_q > cfg.quality.max_negative_fraction:
        anomalies.append("negative_q_fraction")
    if negative_sc > cfg.quality.max_negative_fraction:
        anomalies.append("negative_sc_fraction")

    n_qualifying = len(qualifying)
    qc_pass = n_qualifying >= cfg.quality.min_qualifying_years and not anomalies
    summary = {
        "site_no": str(frame["site_no"].iloc[0]) if "site_no" in frame else None,
        "n_years_qualifying": n_qualifying,
        "median_paired_completeness": float(np.median(paired_values)) if paired_values else None,
        "min_paired_completeness": float(np.min(paired_values)) if paired_values else None,
        "cadence_q_median_min": float(np.median(cad_q_values)) if cad_q_values else None,
        "cadence_sc_median_min": float(np.median(cad_sc_values)) if cad_sc_values else None,
        "sc_median_us_cm": sc_median,
        "sc_p01_us_cm": float(sc_values.quantile(0.01)) if len(sc_values) else None,
        "sc_p99_us_cm": float(sc_values.quantile(0.99)) if len(sc_values) else None,
        "negative_q_fraction": negative_q,
        "negative_sc_fraction": negative_sc,
        "qc_pass": qc_pass,
        "qc_reason": ";".join(anomalies)
        + (
            ";insufficient_qualifying_years"
            if n_qualifying < cfg.quality.min_qualifying_years
            else ""
        ),
    }
    return rows, summary


def run_qc(cfg) -> dict:
    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    eligible = candidates[candidates["candidate_ok"]].sort_values("site_no")
    station_dir = cfg.data_dir / "interim" / "stations"
    year_rows: list[dict] = []
    summaries: list[dict] = []

    for site_no in eligible["site_no"].astype(str):
        path = station_dir / f"{site_no}.parquet"
        if not path.exists():
            summaries.append(
                {
                    "site_no": site_no,
                    "n_years_qualifying": 0,
                    "qc_pass": False,
                    "qc_reason": "no_downloaded_data",
                }
            )
            continue
        frame = read_parquet(path)
        if frame.empty:
            summaries.append(
                {
                    "site_no": site_no,
                    "n_years_qualifying": 0,
                    "qc_pass": False,
                    "qc_reason": "empty_series",
                }
            )
            continue
        frame = frame.copy()
        frame["site_no"] = site_no
        rows, summary = assess_station(frame, cfg)
        for row in rows:
            row["site_no"] = site_no
        year_rows.extend(rows)
        summaries.append(summary)

    results_dir = ensure_dir(cfg.results_dir)
    pd.DataFrame(year_rows).to_csv(results_dir / "qc_summary.csv", index=False)
    summary_frame = pd.DataFrame(summaries)
    summary_frame.to_csv(results_dir / "qc_station_summary.csv", index=False)
    write_json(
        results_dir / "qc_run.json",
        {
            "finished_at": utc_now(),
            "n_stations_assessed": int(len(summary_frame)),
            "n_qc_pass": int(summary_frame["qc_pass"].sum()),
            "n_with_3_years": int(
                (summary_frame["n_years_qualifying"] >= cfg.quality.min_qualifying_years).sum()
            ),
        },
    )
    return {
        "n_stations": len(summary_frame),
        "n_qc_pass": int(summary_frame["qc_pass"].sum()),
    }
