"""Figures for the round-1 screening record."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 7,
        "axes.labelsize": 7,
        "axes.titlesize": 8,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 6,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
    }
)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .hysteresis import event_trajectory  # noqa: E402
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
    fig.savefig(directory / f"{stem}.png", dpi=600, bbox_inches="tight")
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
    _loop_examples(cfg, directory)
    _direction_composition(cfg, directory)
    _area_distribution(cfg, directory)
    _reproducibility(cfg, directory)
    _census_overview(cfg, directory)
    return sorted(path.name for path in directory.glob("*.pdf"))


def _loop_examples(cfg, directory: Path) -> None:
    events_path = cfg.results_dir / "discovery_event_metrics.csv"
    stations_path = cfg.results_dir / "discovery_station_summary.csv"
    if not events_path.exists() or not stations_path.exists():
        return
    events = pd.read_csv(events_path, dtype={"site_no": str})
    selection = pd.read_csv(cfg.experiment_dir / "selection.csv", dtype={"site_no": str})
    strata = selection.set_index("site_no")["stratum"].to_dict()
    events["stratum"] = events["site_no"].map(strata)
    loop = events[events["loop_ok"]].copy()
    if loop.empty:
        return
    chosen: list[pd.Series] = []
    for stratum in cfg.strata.order:
        pool = loop[(loop["stratum"] == stratum) & (loop["direction"] != "ambiguous")]
        if pool.empty:
            continue
        quality = pool[["rise_monotonic", "fall_monotonic", "sc_shape_monotonic"]].min(axis=1)
        pool = pool.assign(quality=quality - 0.5 * pool["secondary_peak_ratio"].clip(0, 1))
        chosen.append(pool.nlargest(1, "quality").iloc[0])
        median_area = pool["loop_area"].median()
        closest = pool.assign(
            distance=(pool["loop_area"] - median_area).abs()
        ).nsmallest(1, "distance")
        chosen.append(closest.iloc[0])
    if not chosen:
        return
    chosen = chosen[:6]
    rows = int(np.ceil(len(chosen) / 2))
    fig, axes = plt.subplots(
        rows, 2, figsize=(8.6, 3.4 * rows), squeeze=False, constrained_layout=True
    )
    for ax, event in zip(axes.ravel(), chosen, strict=False):
        station_path = cfg.data_dir / "interim" / "stations" / f"{event['site_no']}.parquet"
        frame = read_parquet(station_path)
        grid, qn, sn = event_trajectory(
            frame,
            pd.Timestamp(event["start"]),
            pd.Timestamp(event["end"]),
            cfg,
        )
        color = np.linspace(0, 1, len(grid))
        ax.scatter(qn, sn, c=color, cmap="viridis", s=6, zorder=2)
        ax.plot(qn, sn, color="#cccccc", linewidth=0.5, zorder=1)
        peak_index = int(np.argmin(np.abs(grid - pd.Timestamp(event["peak"]))))
        ax.scatter(
            [qn[0], qn[peak_index], qn[-1]],
            [sn[0], sn[peak_index], sn[-1]],
            color=["#009E73", "#D55E00", "#0072B2"],
            s=28,
            zorder=3,
            edgecolor="white",
            linewidth=0.5,
        )
        ax.set_xlabel("Normalized Q")
        ax.set_ylabel("Normalized SC")
        ax.set_title(
            f"{event['site_no']} {event['start'][:10]}\n"
            f"{event['direction']}, area={event['loop_area']:.2f}",
            fontsize=8,
        )
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes.ravel()[len(chosen) :]:
        ax.axis("off")
    fig.suptitle(
        "Discovery loops: best-quality (left) and median-area (right) examples per stratum",
        fontsize=9,
    )
    _save(fig, directory, "fig5_loop_examples")


def _direction_composition(cfg, directory: Path) -> None:
    path = cfg.results_dir / "discovery_station_summary.csv"
    if not path.exists():
        return
    stations = pd.read_csv(path, dtype={"site_no": str})
    stations = stations.sort_values(["direction_fraction", "site_no"])
    fig, ax = plt.subplots(figsize=(7.2, max(4.0, 0.18 * len(stations) + 1.5)))
    y = np.arange(len(stations))
    ax.barh(y, stations["n_clockwise"], color="#0072B2", label="Clockwise")
    ax.barh(
        y,
        stations["n_counterclockwise"],
        left=stations["n_clockwise"],
        color="#D55E00",
        label="Counterclockwise",
    )
    ax.barh(
        y,
        stations["n_ambiguous"],
        left=stations["n_clockwise"] + stations["n_counterclockwise"],
        color="#999999",
        label="Ambiguous",
    )
    labels = [
        f"{row.site_no}{' *' if row.reproducible else ''}"
        for row in stations.itertuples()
    ]
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Loop-usable events")
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    ax.set_title(
        "Direction composition per discovery station (* reproducible)",
        fontsize=10,
        loc="left",
    )
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, directory, "fig6_direction_composition")


def _area_distribution(cfg, directory: Path) -> None:
    events_path = cfg.results_dir / "discovery_event_metrics.csv"
    selection_path = cfg.experiment_dir / "selection.csv"
    if not events_path.exists() or not selection_path.exists():
        return
    events = pd.read_csv(events_path, dtype={"site_no": str})
    selection = pd.read_csv(selection_path, dtype={"site_no": str})
    events["stratum"] = events["site_no"].map(selection.set_index("site_no")["stratum"])
    loop = events[events["loop_ok"]]
    if loop.empty:
        return
    strata = [s for s in cfg.strata.order if (loop["stratum"] == s).any()]
    data = [loop.loc[loop["stratum"] == s, "loop_area"].to_numpy() for s in strata]
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    box = ax.boxplot(
        data,
        tick_labels=[LABELS.get(s, s) for s in strata],
        showfliers=False,
        patch_artist=True,
        widths=0.5,
    )
    for patch, stratum in zip(box["boxes"], strata, strict=False):
        patch.set_facecolor(COLORS.get(stratum, "grey"))
        patch.set_alpha(0.5)
    rng = np.random.default_rng(cfg.seed)
    for index, values in enumerate(data, start=1):
        ax.scatter(
            index + rng.uniform(-0.12, 0.12, len(values)),
            values,
            s=8,
            color=COLORS.get(strata[index - 1], "grey"),
            alpha=0.5,
        )
    ax.set_ylabel("Normalized loop area")
    ax.set_title("Loop area by stratum (discovery events)", fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, directory, "fig7_loop_area")


def _reproducibility(cfg, directory: Path) -> None:
    path = cfg.results_dir / "discovery_station_summary.csv"
    if not path.exists():
        return
    stations = pd.read_csv(path, dtype={"site_no": str})
    stations = stations.dropna(
        subset=["first_half_median_area", "second_half_median_area"]
    )
    stations = stations[
        (stations["first_half_median_area"] > 0)
        & (stations["second_half_median_area"] > 0)
    ]
    if stations.empty:
        return
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    for reproducible, group in stations.groupby("reproducible"):
        ax.scatter(
            group["first_half_median_area"],
            group["second_half_median_area"],
            s=36 if reproducible else 24,
            marker="o" if reproducible else "x",
            color="#009E73" if reproducible else "#999999",
            label="Reproducible" if reproducible else "Not reproducible",
        )
    limit = (
        max(
            stations["first_half_median_area"].max(),
            stations["second_half_median_area"].max(),
        )
        * 1.15
    )
    ax.plot([0, limit], [0, limit], color="#666666", linewidth=0.8, linestyle="--")
    ax.set_xlim(0, limit)
    ax.set_ylim(0, limit)
    ax.set_xlabel("Median loop area, first half")
    ax.set_ylabel("Median loop area, second half")
    ax.set_title("Split-half reproducibility of loop area", fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, directory, "fig8_split_half")


CENSUS_CLASSES = ["A_simple_closed", "B_complex_loop", "C_weak_open", "X_no_usable_sc"]
CENSUS_COLORS = {
    "A_simple_closed": "#0072B2",
    "B_complex_loop": "#E69F00",
    "C_weak_open": "#999999",
    "X_no_usable_sc": "#D55E00",
}
CENSUS_LABELS = {
    "A_simple_closed": "A simple closed",
    "B_complex_loop": "B complex",
    "C_weak_open": "C weak / open",
    "X_no_usable_sc": "X no usable SC",
}


def _census_overview(cfg, directory: Path) -> None:
    event_path = cfg.results_dir / "census_event_metrics.csv"
    station_path = cfg.results_dir / "census_station_summary.csv"
    if not event_path.exists() or not station_path.exists():
        return
    events = pd.read_csv(event_path, dtype={"site_no": str})
    stations = pd.read_csv(station_path, dtype={"site_no": str})
    candidates = read_parquet(cfg.data_dir / "interim" / "candidates.parquet")
    candidates["site_no"] = candidates["site_no"].astype(str)
    events["stratum"] = events["site_no"].map(
        candidates.set_index("site_no")["stratum"]
    )
    fig, axes = plt.subplots(2, 2, figsize=(7.4, 6.0), constrained_layout=True)

    counts = events["class"].value_counts().reindex(CENSUS_CLASSES, fill_value=0)
    ax = axes[0, 0]
    ax.bar(
        [CENSUS_LABELS[key] for key in CENSUS_CLASSES],
        counts.to_numpy(),
        color=[CENSUS_COLORS[key] for key in CENSUS_CLASSES],
    )
    for index, value in enumerate(counts.to_numpy()):
        ax.text(index, value, f"{value:,}", ha="center", va="bottom", fontsize=7)
    ax.set_ylabel("Usable events")
    ax.set_title("Event classes", fontsize=9, loc="left")
    ax.tick_params(axis="x", labelrotation=20, labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[0, 1]
    crosstab = pd.crosstab(events["stratum"], events["class"]).reindex(
        columns=CENSUS_CLASSES, fill_value=0
    )
    crosstab = crosstab.reindex(
        [s for s in cfg.strata.order if s in crosstab.index]
    )
    bottom = np.zeros(len(crosstab))
    for key in CENSUS_CLASSES:
        ax.bar(
            [s.replace("_", " ") for s in crosstab.index],
            crosstab[key].to_numpy(),
            bottom=bottom,
            color=CENSUS_COLORS[key],
            label=CENSUS_LABELS[key],
        )
        bottom += crosstab[key].to_numpy()
    ax.set_ylabel("Usable events")
    ax.set_title("Classes by stratum", fontsize=9, loc="left")
    ax.tick_params(axis="x", labelrotation=15, labelsize=7)
    ax.legend(fontsize=6, frameon=False, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1, 0]
    data = [
        events.loc[events["class"] == key, "event_size"].dropna().to_numpy()
        for key in CENSUS_CLASSES
    ]
    box = ax.boxplot(
        data,
        tick_labels=[CENSUS_LABELS[key] for key in CENSUS_CLASSES],
        showfliers=False,
        patch_artist=True,
        widths=0.55,
    )
    for patch, key in zip(box["boxes"], CENSUS_CLASSES, strict=False):
        patch.set_facecolor(CENSUS_COLORS[key])
        patch.set_alpha(0.45)
    rng = np.random.default_rng(cfg.seed)
    for index, values in enumerate(data, start=1):
        ax.scatter(
            index + rng.uniform(-0.12, 0.12, len(values)),
            values,
            s=3,
            color=CENSUS_COLORS[CENSUS_CLASSES[index - 1]],
            alpha=0.25,
        )
    ax.set_yscale("log")
    ax.set_ylabel("Event size (peak − base Q, cfs)")
    ax.set_title("Event size by class", fontsize=9, loc="left")
    ax.tick_params(axis="x", labelrotation=20, labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1, 1]
    for reproducible, group in stations.groupby("reproducible"):
        ax.scatter(
            group["n_simple"].clip(lower=0.5),
            group["n_weak"].clip(lower=0.5),
            s=22,
            marker="o" if reproducible else "x",
            color="#009E73" if reproducible else "#999999",
            label="Reproducible" if reproducible else "Not reproducible",
        )
    ax.axvline(20, color="#666666", linestyle="--", linewidth=0.7)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Simple loops per station")
    ax.set_ylabel("Weak / open events per station")
    ax.set_title("Station data sufficiency", fontsize=9, loc="left")
    ax.legend(fontsize=6, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)

    _save(fig, directory, "fig9_census_overview")

    source = ensure_dir(cfg.experiment_dir / "descriptive" / "source_data")
    counts.rename_axis("class").reset_index(name="events").to_csv(
        source / "fig9_panel_a.csv", index=False
    )
    crosstab.reset_index().to_csv(source / "fig9_panel_b.csv", index=False)
    events.loc[
        events["class"].isin(CENSUS_CLASSES),
        ["site_no", "event_id", "class", "event_size"],
    ].to_csv(source / "fig9_panel_c.csv", index=False)
    stations[["site_no", "stratum", "n_simple", "n_weak", "reproducible"]].to_csv(
        source / "fig9_panel_d.csv", index=False
    )
