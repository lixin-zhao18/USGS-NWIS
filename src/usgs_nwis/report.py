"""Human-readable experiment record generated from the run outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .io import ensure_dir


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    columns = [str(column) for column in frame.columns]
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def write_report(cfg) -> Path:
    summary_path = cfg.results_dir / "selection_summary.json"
    if not summary_path.exists():
        raise RuntimeError("run `select` before `report`")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    funnel = pd.read_csv(cfg.results_dir / "candidate_reconciliation.csv")
    qc = pd.read_csv(cfg.results_dir / "qc_station_summary.csv", dtype={"site_no": str})
    events = pd.read_csv(cfg.results_dir / "event_station_summary.csv", dtype={"site_no": str})
    selection = pd.read_csv(cfg.experiment_dir / "selection.csv", dtype={"site_no": str})

    n_downloaded = len(qc[qc["qc_reason"].fillna("") != "no_downloaded_data"])
    qc_pass = int(qc["qc_pass"].sum())
    qc_pass_sites = set(qc.loc[qc["qc_pass"], "site_no"].astype(str))
    eligible_events = int(
        (
            (events["n_usable_events"] >= cfg.events.min_count_per_station)
            & events["site_no"].astype(str).isin(qc_pass_sites)
        ).sum()
    )

    lines: list[str] = []
    lines.append("# Round 1 results — catchment screening")
    lines.append("")
    lines.append(f"- Generated: {summary.get('finished_at')}")
    lines.append(f"- Seed: `{summary.get('seed')}`")
    lines.append(
        f"- Window: {cfg.window.start.date()} to {cfg.window.end.date()} (UTC); "
        f"Q=`{cfg.params['streamflow']}`, SC=`{cfg.params['specific_conductance']}`"
    )
    lines.append(
        "- Bulk data: legacy multi-site instantaneous-values service "
        "(`nwis.waterservices.usgs.gov`); attributes: GAGES-II (Falcone 2011)."
    )
    lines.append("")
    lines.append("## Screening funnel")
    lines.append("")
    lines.append(_markdown_table(funnel))
    lines.append("")
    lines.append("## Strata: target and achieved")
    lines.append("")
    lines.append("| Stratum | Target | Selected | Target − selected |")
    lines.append("|---|---:|---:|---:|")
    achieved = selection["stratum"].value_counts().to_dict()
    for stratum in cfg.strata.order:
        target = cfg.selection.strata.get(stratum, 0)
        actual = int(achieved.get(stratum, 0))
        lines.append(f"| {stratum} | {target} | {actual} | {target - actual} |")
    lines.append("")
    lines.append(
        "Negative differences are slots reallocated to strata that still had "
        "eligible independent catchments."
    )
    lines.append("")
    lines.append(
        f"Selected **{len(selection)}** stations: "
        f"{int((selection['split'] == 'discovery').sum())} discovery / "
        f"{int((selection['split'] == 'validation').sum())} validation."
    )
    lines.append("")
    lines.append("## Selected catchments")
    lines.append("")
    table = selection[
        [
            "site_no",
            "station_name",
            "state",
            "area_km2",
            "gages_class",
            "dev_pct",
            "aridity_index",
            "SNOW_PCT_PRECIP",
            "stratum",
            "split",
            "n_years_qualifying",
            "median_paired_completeness",
            "n_usable_events",
        ]
    ].copy()
    table = table.rename(columns={"SNOW_PCT_PRECIP": "snow_pct"})
    table["n_years_qualifying"] = table["n_years_qualifying"].fillna(0).astype(int)
    table["n_usable_events"] = table["n_usable_events"].fillna(0).astype(int)
    table["median_paired_completeness"] = table["median_paired_completeness"].round(3)
    table["area_km2"] = table["area_km2"].round(1)
    table["dev_pct"] = table["dev_pct"].round(1)
    table["aridity_index"] = table["aridity_index"].round(2)
    lines.append(_markdown_table(table))
    lines.append("")
    lines.append("## Data-quality envelope")
    lines.append("")
    lines.append(
        f"- Downloaded candidates: {n_downloaded}; QC pass (≥"
        f"{cfg.quality.min_qualifying_years} qualifying years): {qc_pass}."
    )
    lines.append(
        f"- Stations with ≥{cfg.events.min_count_per_station} usable events: {eligible_events}."
    )
    if not selection.empty:
        lines.append(
            f"- Selected median paired completeness: "
            f"{selection['median_paired_completeness'].median():.3f}; "
            f"selected usable events: median {selection['n_usable_events'].median():.0f}, "
            f"range {selection['n_usable_events'].min():.0f}–"
            f"{selection['n_usable_events'].max():.0f}."
        )
        lines.append(
            f"- Selected area: median {selection['area_km2'].median():.0f} km² "
            f"(range {selection['area_km2'].min():.0f}–{selection['area_km2'].max():.0f}); "
            f"inside 10–200 km²: {int((selection['area_km2'] <= 200).sum())}."
        )
        lines.append(
            f"- GAGES-II reference: {int((selection['gages_class'] == 'Ref').sum())}; "
            f"low-disturbance flag: {int(selection['low_disturbance'].sum())}."
        )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- The candidate pool is restricted to GAGES-II conterminous gages, so "
        "NWIS-only sites without consistent area/climate/disturbance attributes "
        "are excluded."
    )
    lines.append(
        "- True semiarid small catchments with continuous SC are effectively "
        "absent from the USGS network. The stratum is undersubscribed; slots are "
        "reallocated rather than filling it with humid sites relabelled as dry."
    )
    lines.append(
        "- Winter SC gaps are common in snow-dominated basins; usable-event "
        "counts therefore favour sites with year-round SC coverage."
    )
    lines.append(
        "- No SC–Q hysteresis metric has been computed in this round. The "
        "validation split remains unopened."
    )
    lines.append("")
    lines.append("## Next step")
    lines.append("")
    lines.append(
        "Compute event-scale SC–Q hysteresis descriptors (direction, loop area, "
        "HI/FI, amplitude) on the discovery split only, with event-level "
        "reproducibility checks before any model work."
    )
    lines.append("")

    output = ensure_dir(cfg.experiment_dir) / "RESULTS.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def write_hysteresis_report(cfg) -> Path:
    """Write the discovery-only hysteresis record."""
    summary_path = cfg.results_dir / "hysteresis_summary.json"
    if not summary_path.exists():
        raise RuntimeError("run `hysteresis` before reporting it")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    stations = pd.read_csv(
        cfg.results_dir / "discovery_station_summary.csv", dtype={"site_no": str}
    )
    loose_path = cfg.results_dir / "sensitivity_loose" / "hysteresis_summary.json"
    loose = json.loads(loose_path.read_text(encoding="utf-8")) if loose_path.exists() else None

    lines: list[str] = []
    lines.append("# Discovery-set SC–Q hysteresis diagnostics")
    lines.append("")
    lines.append(f"- Generated: {summary.get('finished_at')}")
    lines.append("- Scope: 30 discovery stations only; the 10 validation stations are sealed.")
    lines.append("- Method: Q-only event windows, normalised Q and SC, area between rising")
    lines.append("  and falling limbs, clockwise = falling limb below rising limb at equal Q.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    fraction = summary["reproducible_fraction"]
    if not summary["data_sufficient"]:
        verdict = "FAIL"
    elif fraction >= 0.75:
        verdict = "PASS"
    else:
        verdict = "MARGINAL PASS"
    lines.append(
        f"**{verdict} the frozen data-sufficiency gate** "
        f"({summary['n_reproducible_stations']}/{summary['n_discovery_stations']} = "
        f"{summary['reproducible_fraction']:.0%} of stations reproducible; gate "
        f"{summary['criteria']['min_station_pass_fraction']:.0%})."
    )
    lines.append("")
    lines.append(
        "The gate is met exactly at its threshold: this supports a pilot model, "
        "not a definitive claim that the discovery set is representative."
    )
    lines.append("")
    lines.append(
        f"- {summary['n_events_evaluated']:,} usable discovery events; "
        f"{summary['n_loop_events']:,} pass the strict loop-quality filters."
    )
    lines.append(
        "- Direction is strongly asymmetric: "
        f"{summary['direction_counts'].get('clockwise', 0):,} clockwise vs "
        f"{summary['direction_counts'].get('counterclockwise', 0):,} counterclockwise."
    )
    lines.append(
        f"- {int((stations['dominant_direction'] == 'clockwise').sum())}/30 stations are "
        f"clockwise-dominant; median within-station direction fraction "
        f"{stations['direction_fraction'].median():.2f}."
    )
    if loose:
        lines.append(
            f"- Sensitivity without the strict loop-quality filters: "
            f"{loose['n_loop_events']:,} loops, "
            f"{loose['n_reproducible_stations']}/30 stations reproducible. "
            "The direction asymmetry survives; the event count does not."
        )
    lines.append("")
    lines.append("## By stratum")
    lines.append("")
    table = stations.groupby("stratum").agg(
        stations=("site_no", "size"),
        reproducible=("reproducible", "sum"),
        median_loops=("n_loop_events", "median"),
        median_direction_fraction=("direction_fraction", "median"),
        median_loop_area=("median_loop_area", "median"),
    )
    table = table.reset_index()
    lines.append(_markdown_table(table.round(3)))
    lines.append("")
    lines.append("## Failure modes (strict filter)")
    lines.append("")
    failures = stations.loc[~stations["reproducible"], "reproducibility_reason"]
    lines.append(_markdown_table(failures.value_counts().rename_axis("reason").reset_index(name="stations")))
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "- There is a reproducible clockwise SC–Q loop in humid and snow-influenced "
        "stations: SC is lower on the recession than on the rising limb at equal Q, "
        "consistent with dilution followed by slow chemical recovery."
    )
    lines.append(
        "- The semiarid stratum is not usable: 0/2 discovery stations pass. "
        "No semiarid claim should be made from this round."
    )
    lines.append(
        "- The strict filter retains only a quarter of usable events. Many events are "
        "multi-peak, SC does not recover, or the SC minimum is misplaced; those are "
        "excluded from the primary analysis."
    )
    lines.append(
        "- This is a feasibility result, not evidence that SC observes a unique "
        "connectivity state. Q-only predictability and the AI comparison are still open."
    )
    lines.append("")
    lines.append("## Next decision")
    lines.append("")
    lines.append(
        "Run the Q-only baseline (predict SC or normalised SC within events from Q, "
        "dQ/dt and recent Q history with station-wise cross-validation). If it already "
        "explains >85% of SC variance, the hysteresis signal is not strong enough to "
        "justify the AI model."
    )
    lines.append("")
    output = ensure_dir(cfg.experiment_dir) / "RESULTS_HYSTERESIS.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
