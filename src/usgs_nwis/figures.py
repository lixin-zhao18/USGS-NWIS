"""Figures for the round-1 screening record."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .io import ensure_dir, read_parquet  # noqa: E402

COLORS = {
    "humid_rainfall": "#0072B2",
    "snow_mixed": "#56B4E9",
    "semiarid_flashy": "#D55E00",
}
LABELS = {
    "humid_rainfall": "Humid rainfall",
    "snow_mixed": "Snow / rain-snow",
    "semiarid_flashy": "Semiarid / dry flashy",
}


def _save(fig, directory: Path, stem: str) -> None:
    fig.savefig(directory / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(directory / f"{stem}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def _funnel(cfg, directory: Path) -> None:
    path = cfg.results_dir / "candidate_reconciliation.csv"
    if not path.exists():
        return
    frame = pd.read_csv(path)
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    positions = np.arange(len(frame))
    ax.barh(positions, frame["n_sites"], color="#4C72B0")
    ax.set_yticks(positions)
    ax.set_yticklabels([step.replace("_", " ") for step in frame["step"]], fontsize=8)
    ax.invert_yaxis()
    for position, value in zip(positions, frame["n_sites"], strict=True):
        ax.text(value + max(frame["n_sites"]) * 0.01, position, str(value), va="center", fontsize=8)
    ax.set_xlabel("Number of stations")
    ax.set_title("Round 1 screening funnel", fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, directory, "fig1_screening_funnel")


def _map(cfg, selection: pd.DataFrame, directory: Path) -> None:
    if selection.empty:
        return
    fig, ax = plt.subplots(figsize=(8.0, 4.6))
    for stratum, group in selection.groupby("stratum"):
        for split, marker in (("discovery", "o"), ("validation", "s")):
            subset = group[group["split"] == split]
            if subset.empty:
                continue
            ax.scatter(
                subset["lon"],
                subset["lat"],
                s=34,
                marker=marker,
                color=COLORS.get(stratum, "grey"),
                edgecolor="white" if split == "discovery" else "black",
                linewidth=0.6,
                label=f"{LABELS.get(stratum, stratum)} — {split}",
                alpha=0.9,
            )
    ax.set_xlim(-126, -66)
    ax.set_ylim(24, 50)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(color="#dddddd", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=7, loc="lower left", frameon=False, ncol=2)
    ax.set_title(
        "Selected catchments (circles: discovery; squares: validation)",
        fontsize=10,
        loc="left",
    )
    _save(fig, directory, "fig2_selected_map")


def _qc_scatter(cfg, directory: Path) -> None:
    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    qc_path = cfg.results_dir / "qc_station_summary.csv"
    events_path = cfg.results_dir / "event_station_summary.csv"
    if not qc_path.exists() or not events_path.exists():
        return
    qc = pd.read_csv(qc_path, dtype={"site_no": str})
    events = pd.read_csv(events_path, dtype={"site_no": str})
    frame = candidates[candidates["candidate_ok"]].copy()
    frame["site_no"] = frame["site_no"].astype(str)
    frame = frame.merge(qc, on="site_no", how="left").merge(events, on="site_no", how="left")
    selection_path = cfg.experiment_dir / "selection.csv"
    selected = set()
    if selection_path.exists():
        selected = set(pd.read_csv(selection_path, dtype={"site_no": str})["site_no"])

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    for stratum, group in frame.groupby("stratum"):
        ax.scatter(
            group["median_paired_completeness"],
            group["n_usable_events"].fillna(0),
            s=18,
            color=COLORS.get(stratum, "grey"),
            alpha=0.55,
            label=LABELS.get(stratum, stratum),
        )
    chosen = frame[frame["site_no"].isin(selected)]
    ax.scatter(
        chosen["median_paired_completeness"],
        chosen["n_usable_events"].fillna(0),
        s=70,
        facecolors="none",
        edgecolors="black",
        linewidths=1.0,
        label="Selected",
    )
    ax.axhline(cfg.events.min_count_per_station, color="#666666", linestyle="--", linewidth=0.8)
    ax.axvline(cfg.quality.min_paired_completeness, color="#666666", linestyle="--", linewidth=0.8)
    ax.set_yscale("symlog", linthresh=10)
    ax.set_xlabel("Median paired completeness in qualifying years")
    ax.set_ylabel("Usable events (10 years)")
    ax.set_title("Data feasibility of downloaded candidates", fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=7, frameon=False)
    _save(fig, directory, "fig3_qc_events")


def _composition(cfg, selection: pd.DataFrame, directory: Path) -> None:
    if selection.empty:
        return
    achieved = selection["stratum"].value_counts()
    strata = [s for s in cfg.strata.order if s in cfg.selection.strata]
    target = [cfg.selection.strata[s] for s in strata]
    actual = [int(achieved.get(s, 0)) for s in strata]
    x = np.arange(len(strata))
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.bar(x - 0.2, target, width=0.4, color="#cccccc", label="Target")
    ax.bar(
        x + 0.2,
        actual,
        width=0.4,
        color=[COLORS.get(s, "grey") for s in strata],
        label="Achieved",
    )
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS.get(s, s) for s in strata], fontsize=8)
    ax.set_ylabel("Stations")
    ax.set_title("Stratum composition (shortfall reported, not relaxed)", fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, directory, "fig4_stratum_composition")


def make_figures(cfg) -> list[str]:
    directory = ensure_dir(cfg.figures_dir)
    _funnel(cfg, directory)
    selection_path = cfg.experiment_dir / "selection.csv"
    if selection_path.exists():
        selection = pd.read_csv(selection_path, dtype={"site_no": str})
        _map(cfg, selection, directory)
        _composition(cfg, selection, directory)
    _qc_scatter(cfg, directory)
    return sorted(path.name for path in directory.glob("*.pdf"))
