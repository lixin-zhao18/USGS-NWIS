from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from usgs_nwis.config import load_config
from usgs_nwis.events import detect_events, extract_site_events
from usgs_nwis.qc import assess_year, cadence_minutes
from usgs_nwis.selection import _allocate_validation


def _cfg():
    return load_config(Path(__file__).resolve().parents[1] / "configs" / "round1.yaml")


def _series(year: int, step_min: int = 15, gaps: bool = False) -> pd.DataFrame:
    index = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:45", freq=f"{step_min}min", tz="UTC")
    values = np.ones(len(index))
    frame = pd.DataFrame({"time": index, "q_cfs": values, "sc_us_cm": 100 + values})
    if gaps:
        # Remove a contiguous middle block so the modal cadence stays 15 min.
        cut = frame.iloc[int(len(frame) * 0.2) : int(len(frame) * 0.8)]
        frame = frame.drop(cut.index).reset_index(drop=True)
    return frame


def test_cadence_minutes() -> None:
    times = pd.Series(
        pd.date_range("2020-01-01", periods=10, freq="15min", tz="UTC")
    )
    assert cadence_minutes(times) == 15.0


def test_complete_year_qualifies() -> None:
    frame = _series(2020)
    row = assess_year(
        frame[["time", "q_cfs"]],
        frame[["time", "sc_us_cm"]],
        2020,
        max_cadence=60,
        min_paired=0.8,
    )
    assert row["qualifying"] is True
    assert row["paired_completeness"] > 0.99


def test_half_year_gap_fails() -> None:
    frame = _series(2020, gaps=True)
    row = assess_year(
        frame[["time", "q_cfs"]],
        frame[["time", "sc_us_cm"]],
        2020,
        max_cadence=60,
        min_paired=0.8,
    )
    assert row["qualifying"] is False
    assert row["paired_completeness"] < 0.6


def test_coarse_cadence_fails() -> None:
    frame = _series(2020, step_min=120)
    row = assess_year(
        frame[["time", "q_cfs"]],
        frame[["time", "sc_us_cm"]],
        2020,
        max_cadence=60,
        min_paired=0.8,
    )
    assert row["reason"] == "cadence_too_coarse"


def test_event_detection_and_merge() -> None:
    cfg = _cfg()
    index = pd.date_range("2020-01-01", "2020-12-31 23:00", freq="1h", tz="UTC")
    q = pd.Series(5.0, index=index)
    # Two storm events with a nested secondary peak.
    for start, scale in ((index[100], 50.0), (index[400], 100.0)):
        for offset, factor in enumerate((1, 3, 8, 5, 2, 1.2)):
            q.loc[start + timedelta(hours=offset)] = scale * factor
    events = detect_events(q, cfg)
    assert len(events) == 2
    assert events["peak_q"].max() > 500


def test_site_events_flag_sc_gaps() -> None:
    cfg = _cfg()
    frame = _series(2020, step_min=60)
    # Inject one event with a multi-point rising limb.
    profile = [10, 20, 50, 100, 300, 500, 300, 100, 40, 20, 10, 8, 6]
    frame.loc[100 : 100 + len(profile) - 1, "q_cfs"] = profile
    frame.loc[100 : 100 + len(profile) - 1, "sc_us_cm"] = np.nan
    events = extract_site_events(frame, "USGS-TEST", cfg)
    assert not events.empty
    assert not events["usable"].any()


def test_validation_allocation_respects_total() -> None:
    counts = {"humid_rainfall": 26, "snow_mixed": 10, "semiarid_flashy": 4}
    allocation = _allocate_validation(counts, 10)
    assert sum(allocation.values()) == 10
    assert all(0 <= allocation[key] < counts[key] for key in counts)


def test_validation_allocation_protects_small_stratum() -> None:
    allocation = _allocate_validation(
        {"humid_rainfall": 28, "snow_mixed": 10, "semiarid_flashy": 2}, 10
    )
    assert allocation["semiarid_flashy"] == 0
    assert sum(allocation.values()) == 10


def test_validation_allocation_keeps_rare_stratum() -> None:
    allocation = _allocate_validation(
        {"humid_rainfall": 23, "snow_mixed": 14, "semiarid_flashy": 3}, 10
    )
    assert allocation["semiarid_flashy"] >= 1
    assert sum(allocation.values()) == 10


def test_event_metrics_direction_and_area() -> None:
    from types import SimpleNamespace

    from usgs_nwis.hysteresis import event_metrics

    cfg = _cfg()
    index = pd.date_range("2020-01-01", periods=192, freq="15min", tz="UTC")
    q = np.ones(len(index))
    sc = np.full(len(index), 100.0)
    # 12 h base, 6 h rise to 10, 12 h recession, then base again.
    q[48:73] = np.linspace(1, 10, 25)
    q[73:121] = np.linspace(10, 1, 48)
    sc[48:73] = np.linspace(100, 50, 25)
    sc[73:121] = np.linspace(50, 95, 48)
    frame = pd.DataFrame({"time": index, "q_cfs": q, "sc_us_cm": sc})
    event = SimpleNamespace(
        site_no="USGS-TEST",
        event_id="USGS-TEST_2020_001",
        start=index[48],
        peak=index[72],
        end=index[120],
        duration_h=18.0,
        peak_q=10.0,
        base_q=1.0,
    )
    record = event_metrics(frame, event, cfg)
    assert record["loop_ok"] is True
    assert record["direction"] == "clockwise"
    assert record["loop_area"] > 0
    assert record["sc_recovery"] > 0.5
