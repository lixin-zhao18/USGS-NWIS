"""Stratified, seeded station selection with documented shortfall handling."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .io import ensure_dir, read_parquet, utc_now, write_json

_STOP_WORDS = {
    "RIVER", "CREEK", "CR", "CK", "FORK", "BRANCH", "BR", "NEAR", "AT", "ABOVE",
    "BELOW", "NR", "ABV", "BLW", "STATE", "HIGHWAY", "HWY", "ROAD", "RD",
    "NORTH", "SOUTH", "EAST", "WEST", "BIG", "LITTLE", "MIDDLE", "UPPER",
    "LOWER", "OF", "THE", "AND", "COUNTY", "CO", "DAM", "LAKE", "POND",
    "SPRING", "CITY", "TOWN", "ST",
}


def _river_key(name: str) -> str:
    """First distinctive name token, used to spot nested gauges."""
    for token in re.findall(r"[A-Z]{2,}", str(name).upper()):
        if token not in _STOP_WORDS and not token.isdigit():
            return token
    return ""


def _score(frame: pd.DataFrame, cfg, rng: np.random.Generator) -> pd.Series:
    weights = cfg.selection.rank_weights
    score = pd.Series(0.0, index=frame.index)
    if "CLASS" in frame:
        score += weights.get("reference", 0.0) * (frame["CLASS"] == "Ref").astype(float)
    if "preferred_area" in frame:
        score += weights.get("preferred_area", 0.0) * frame["preferred_area"].astype(float)
    if "low_disturbance" in frame:
        score += weights.get("low_disturbance", 0.0) * frame["low_disturbance"].astype(float)
    dev = frame.get("DEVNLCD06", pd.Series(0.0, index=frame.index)).fillna(
        cfg.candidate.max_dev_pct
    )
    score += weights.get("low_dev", 0.0) * (
        1.0 - dev.clip(0, cfg.candidate.max_dev_pct) / cfg.candidate.max_dev_pct
    )
    dams = frame.get("DDENS_2009", pd.Series(0.0, index=frame.index)).fillna(
        cfg.candidate.max_dam_density
    )
    score += weights.get("low_dams", 0.0) * (
        1.0 - dams.clip(0, cfg.candidate.max_dam_density) / cfg.candidate.max_dam_density
    )
    completeness = frame.get(
        "median_paired_completeness", pd.Series(0.0, index=frame.index)
    ).fillna(0.0)
    score += weights.get("completeness", 0.0) * completeness.clip(0, 1)
    events = frame.get("n_usable_events", pd.Series(0.0, index=frame.index)).fillna(0.0)
    score += weights.get("events", 0.0) * (events.clip(0, 200) / 200.0)
    jitter = rng.uniform(0.0, 1.0, len(frame))
    score += weights.get("jitter", 0.0) * jitter
    return score


def _allocate_validation(counts: dict[str, int], total: int) -> dict[str, int]:
    """Allocate validation slots, keeping every represented stratum in validation.

    Strata with at least three selected stations spare at least one validation
    station; strata with one or two stations stay entirely in discovery.
    """
    allocation = {key: 0 for key in counts}
    strata = [key for key, value in counts.items() if value >= 3]
    if not strata or total <= 0:
        return allocation
    capacity = sum(counts[key] - 1 for key in strata)
    total = min(total, capacity)
    ideal = {key: counts[key] * total / sum(counts[key] for key in strata) for key in strata}
    for key in strata:
        allocation[key] = int(np.floor(ideal[key]))
    remainder = total - sum(allocation.values())
    ranked = sorted(strata, key=lambda key: ideal[key] - allocation[key], reverse=True)
    for key in ranked[:remainder]:
        allocation[key] += 1
    for key in strata:
        allocation[key] = min(allocation[key], counts[key] - 1)
    # Guarantee representation of every stratum that can spare a station.
    for key in strata:
        if allocation[key] == 0 and total >= len(strata):
            allocation[key] = 1
    difference = total - sum(allocation.values())
    ordered = sorted(strata, key=lambda key: counts[key], reverse=True)
    cursor = 0
    while difference != 0 and ordered:
        key = ordered[cursor % len(ordered)]
        if difference > 0 and allocation[key] < counts[key] - 1:
            allocation[key] += 1
            difference -= 1
        elif difference < 0 and allocation[key] > 1:
            allocation[key] -= 1
            difference += 1
        cursor += 1
        if cursor > len(ordered) * 20:
            break
    return allocation


def _funnel(
    candidates: pd.DataFrame,
    station_dir,
    qc: pd.DataFrame,
    events: pd.DataFrame,
    cfg,
) -> list[dict]:
    steps = [("catalog_sites_both_params", pd.Series(True, index=candidates.index))]
    window_start = pd.Timestamp(cfg.window.start).tz_localize(None)
    window_end = pd.Timestamp(cfg.window.end).tz_localize(None)
    overlap_ok = (
        (candidates["overlap_years"] >= 3)
        & candidates["overlap_end"].notna()
        & (candidates["overlap_end"] >= pd.Timestamp(cfg.window.end.year, 1, 1))
        & (candidates["overlap_start"] <= window_end)
        & (candidates["overlap_end"] >= window_start)
    )
    steps.append(("catalog_overlap_ge_3y", overlap_ok))
    gages_ok = candidates["DRAIN_SQKM"].notna()
    steps.append(("gages2_matched", overlap_ok & gages_ok))
    area_ok = candidates["DRAIN_SQKM"].between(
        cfg.candidate.area_min_km2, cfg.candidate.area_max_km2
    )
    steps.append(("area_within_range", overlap_ok & gages_ok & area_ok))
    name_ok = ~candidates["exclusion_reason"].fillna("").str.contains("name_flag")
    steps.append(("name_flags_clear", overlap_ok & gages_ok & area_ok & name_ok))
    disturbance_ok = (
        (candidates["DEVNLCD06"].fillna(0) <= cfg.candidate.max_dev_pct)
        & (candidates["DDENS_2009"].fillna(0) <= cfg.candidate.max_dam_density)
    )
    steps.append(
        (
            "disturbance_limits",
            overlap_ok & gages_ok & area_ok & name_ok & disturbance_ok,
        )
    )
    candidate_ok = candidates["candidate_ok"]
    steps.append(("candidate_ok", candidate_ok))
    downloaded = candidates["site_no"].astype(str).isin(
        [path.stem for path in station_dir.glob("*.parquet")]
    )
    steps.append(("downloaded", candidate_ok & downloaded))
    qc_ok = candidates["site_no"].astype(str).isin(
        qc.loc[qc["qc_pass"], "site_no"].astype(str)
    )
    steps.append(("qc_pass", candidate_ok & downloaded & qc_ok))
    event_ok_sites = events.loc[
        events["n_usable_events"] >= cfg.events.min_count_per_station, "site_no"
    ].astype(str)
    events_ok = candidates["site_no"].astype(str).isin(event_ok_sites)
    steps.append(("usable_events_ge_min", candidate_ok & downloaded & qc_ok & events_ok))
    return [{"step": name, "n_sites": int(mask.sum())} for name, mask in steps]


def run_selection(cfg) -> pd.DataFrame:
    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    qc_path = cfg.results_dir / "qc_station_summary.csv"
    event_path = cfg.results_dir / "event_station_summary.csv"
    if not qc_path.exists() or not event_path.exists():
        raise RuntimeError("run `qc` and `events` before `select`")
    qc = pd.read_csv(qc_path, dtype={"site_no": str})
    events = pd.read_csv(event_path, dtype={"site_no": str})

    frame = candidates.copy()
    frame["site_no"] = frame["site_no"].astype(str)
    frame = frame.merge(qc, on="site_no", how="left", suffixes=("", "_qc"))
    frame = frame.merge(events, on="site_no", how="left")

    eligible = frame[
        frame["candidate_ok"]
        & frame["qc_pass"].fillna(False).astype(bool)
        & (frame["n_usable_events"].fillna(0) >= cfg.events.min_count_per_station)
    ].copy()

    rng = np.random.default_rng(cfg.seed)
    eligible["selection_score"] = _score(eligible, cfg, rng)
    eligible = eligible.sort_values(
        ["stratum", "selection_score"], ascending=[True, False]
    )
    eligible["huc8"] = eligible["huc"].astype(str).str[:8]

    huc_counts: dict[str, int] = {}
    river_keys: set[tuple[str, str]] = set()
    max_per_huc8 = max(1, cfg.selection.max_per_huc8)

    def _pick(pool: pd.DataFrame, count: int) -> pd.DataFrame:
        picked: list[pd.Series] = []
        for _, row in pool.iterrows():
            huc8 = str(row["huc"])[:8]
            river = _river_key(row["station_name"])
            if huc_counts.get(huc8, 0) >= max_per_huc8:
                continue
            if (huc8, river) in river_keys:
                continue
            picked.append(row)
            huc_counts[huc8] = huc_counts.get(huc8, 0) + 1
            river_keys.add((huc8, river))
            if len(picked) >= count:
                break
        return pd.DataFrame(picked) if picked else pool.iloc[0:0]

    selected_frames: list[pd.DataFrame] = []
    shortfall: dict[str, int] = {}
    for stratum in cfg.strata.order:
        target = cfg.selection.strata.get(stratum, 0)
        pool = eligible[eligible["stratum"] == stratum]
        picked = _pick(pool, target)
        shortfall[stratum] = target - len(picked)
        if len(picked):
            selected_frames.append(picked)
    selected = (
        pd.concat(selected_frames, ignore_index=True)
        if selected_frames
        else eligible.iloc[0:0].copy()
    )

    remaining_slots = cfg.selection.target_total - len(selected)
    for stratum in cfg.selection.reallocation_order:
        if remaining_slots <= 0:
            break
        already = set(selected["site_no"])
        pool = eligible[
            (eligible["stratum"] == stratum) & (~eligible["site_no"].isin(already))
        ]
        picked = _pick(pool, remaining_slots)
        if len(picked):
            selected = pd.concat([selected, picked], ignore_index=True)
            remaining_slots -= len(picked)

    selected = selected.sort_values(["stratum", "selection_score"], ascending=[True, False])
    selected["selection_rank"] = selected.groupby("stratum").cumcount() + 1

    counts = selected["stratum"].value_counts().to_dict()
    validation_total = min(
        cfg.selection.target_total // 4,
        max(1, int(round(0.25 * len(selected)))),
    )
    validation = _allocate_validation(counts, validation_total) if counts else {}
    split_rng = np.random.default_rng(cfg.seed + 1)
    selected["split"] = "discovery"
    for stratum, n_val in validation.items():
        indices = selected.index[selected["stratum"] == stratum].to_list()
        if not indices or n_val <= 0:
            continue
        chosen = split_rng.choice(indices, size=min(n_val, len(indices)), replace=False)
        selected.loc[chosen, "split"] = "validation"

    columns = [
        "site_no",
        "station_name",
        "state",
        "lat",
        "lon",
        "huc",
        "huc8",
        "DRAIN_SQKM",
        "CLASS",
        "DEVNLCD06",
        "DDENS_2009",
        "HYDRO_DISTURB_INDX",
        "aridity_index",
        "SNOW_PCT_PRECIP",
        "PPTAVG_BASIN",
        "stratum",
        "split",
        "selection_rank",
        "selection_score",
        "n_years_qualifying",
        "median_paired_completeness",
        "cadence_q_median_min",
        "cadence_sc_median_min",
        "sc_median_us_cm",
        "n_events",
        "n_usable_events",
        "low_disturbance",
        "preferred_area",
        "ts_id_q",
        "ts_id_sc",
        "begin_q",
        "end_q",
        "begin_sc",
        "end_sc",
    ]
    output = selected[[column for column in columns if column in selected.columns]].copy()
    output = output.rename(
        columns={
            "DRAIN_SQKM": "area_km2",
            "CLASS": "gages_class",
            "DEVNLCD06": "dev_pct",
            "DDENS_2009": "dam_density",
            "ts_id_q": "catalog_ts_id_q",
            "ts_id_sc": "catalog_ts_id_sc",
        }
    )
    if "selection_score" in output:
        output["selection_score"] = output["selection_score"].round(2)
    results_dir = ensure_dir(cfg.results_dir)
    output.to_csv(cfg.experiment_dir / "selection.csv", index=False)

    reasons = frame[
        ["site_no", "station_name", "state", "stratum", "exclusion_reason", "qc_reason"]
    ].copy()
    reasons["final_status"] = np.where(
        reasons["site_no"].isin(output["site_no"]), "selected", "not_selected"
    )
    reasons.loc[
        reasons["final_status"].eq("not_selected")
        & reasons["exclusion_reason"].fillna("").eq("")
        & reasons["qc_reason"].fillna("").eq(""),
        "final_status",
    ] = "eligible_but_below_cutoff"
    reasons.to_csv(results_dir / "exclusion_summary.csv", index=False)

    funnel = _funnel(
        candidates, cfg.data_dir / "interim" / "stations", qc, events, cfg
    )
    funnel.append({"step": "selected", "n_sites": int(len(output))})
    pd.DataFrame(funnel).to_csv(results_dir / "candidate_reconciliation.csv", index=False)

    summary = {
        "finished_at": utc_now(),
        "target_total": cfg.selection.target_total,
        "n_selected": int(len(output)),
        "n_discovery": int((output["split"] == "discovery").sum()),
        "n_validation": int((output["split"] == "validation").sum()),
        "by_stratum": counts,
        "by_stratum_split": {
            f"{stratum}|{split}": int(value)
            for (stratum, split), value in output.groupby(["stratum", "split"])
            .size()
            .items()
        },
        "shortfall": shortfall,
        "n_eligible": int(len(eligible)),
        "seed": cfg.seed,
    }
    write_json(results_dir / "selection_summary.json", summary)
    return output


_FORBIDDEN_SELECTION_COLUMNS = {
    "loop_area",
    "normalized_loop_area",
    "hysteresis_index",
    "hi",
    "fi",
    "direction",
    "clockwise",
    "hysteresis_direction",
}


def validate_selection(cfg) -> dict:
    """Check that selection.csv obeys the preregistered separation rules."""
    path = cfg.experiment_dir / "selection.csv"
    if not path.exists():
        raise RuntimeError("run `select` before `validate`")
    selection = pd.read_csv(path, dtype={"site_no": str})
    problems: list[str] = []
    forbidden = sorted(_FORBIDDEN_SELECTION_COLUMNS & set(selection.columns))
    if forbidden:
        problems.append(f"hysteresis-derived columns present: {forbidden}")
    if (selection["n_years_qualifying"] < cfg.quality.min_qualifying_years).any():
        problems.append("station below qualifying-year minimum")
    if (selection["n_usable_events"] < cfg.events.min_count_per_station).any():
        problems.append("station below usable-event minimum")
    if "huc8" in selection:
        counts = selection.groupby("huc8").size()
        if (counts > cfg.selection.max_per_huc8).any():
            problems.append("HUC8 cap exceeded")
    if "station_name" in selection and "huc8" in selection:
        keys = selection.apply(
            lambda row: (row["huc8"], _river_key(row["station_name"])), axis=1
        )
        if keys.duplicated().any():
            problems.append("duplicate river key inside a HUC8")
    summary = {
        "n_selected": int(len(selection)),
        "n_discovery": int((selection["split"] == "discovery").sum()),
        "n_validation": int((selection["split"] == "validation").sum()),
        "ok": not problems,
        "problems": problems,
    }
    write_json(cfg.results_dir / "selection_checks.json", summary)
    return summary
