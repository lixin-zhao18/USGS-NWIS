"""Command-line entry points."""

from __future__ import annotations

import time
from pathlib import Path

import typer

from .catalog import build_candidates
from .config import load_config
from .download import download_all
from .events import run_events
from .figures import make_figures
from .forcing import AORC_VARIABLES, CORE_VARIABLES, a_class_events, extract_aorc, extract_nldas
from .hysteresis import run_census, run_hysteresis
from .pipeline import run_pipeline
from .qc import run_qc
from .report import write_report
from .selection import run_selection, validate_selection
from .training_audit import (
    build_hourly,
    build_training_catalog,
    download_training,
    run_network_smoke,
)
from .training_audit import (
    build_report as build_training_audit_report,
)

app = typer.Typer(add_completion=False, help="Reproducible USGS Q–SC catchment screening.")
training_audit_app = typer.Typer(
    add_completion=False,
    help="Audit and build UTC-hourly paired Q–SC training data.",
)
app.add_typer(training_audit_app, name="training-audit")


def _config(path: Path):
    return load_config(path)


@training_audit_app.command("catalog")
def training_catalog_command(
    config: Path = typer.Option(Path("configs/training_audit.yaml")),
    refresh: bool = typer.Option(False, help="Re-download state catalogs."),
) -> None:
    """Build the unfiltered Q/SC catalog population and catalog-overlap layer."""
    frame = build_training_catalog(_config(config), refresh=refresh)
    typer.echo(
        f"union={len(frame)} q={int(frame['catalog_q'].sum())} "
        f"sc={int(frame['catalog_sc'].sum())} paired={int(frame['catalog_paired'].sum())} "
        f"window_overlap={int(frame['window_overlap'].sum())}"
    )


@training_audit_app.command("download")
def training_download_command(
    config: Path = typer.Option(Path("configs/training_audit.yaml")),
    refresh: bool = typer.Option(False, help="Re-download completed site-years."),
    sites_limit: int | None = typer.Option(None, help="Limit sites for a smoke run."),
    site_offset: int = typer.Option(0, help="Skip this many sorted candidate sites."),
    years: str | None = typer.Option(None, help="Inclusive range, e.g. 2023-2024."),
    batch_sites: int | None = typer.Option(None, help="Sites per USGS request batch."),
    cache_only: bool = typer.Option(False, help="Only reuse verified cache; no network."),
    no_resume: bool = typer.Option(False, help="Ignore completion markers."),
    min_free_disk_gb: float | None = typer.Option(None, help="Stop a batch below this free space."),
    prune_new_raw: bool = typer.Option(
        False, help="Delete only newly downloaded raw files after verified hourly output."
    ),
) -> None:
    """Download raw observations while preserving all USGS quality qualifiers."""
    chosen_years = None
    if years:
        start, end = (int(value) for value in years.split("-"))
        chosen_years = list(range(start, end + 1))
    started = time.monotonic()

    def report(snapshot: dict) -> None:
        elapsed = time.monotonic() - started
        done = (
            snapshot["cached"]
            + snapshot["site_years_completed"]
            + snapshot["no_data"]
            + snapshot["failed"]
            + snapshot["skipped"]
        )
        rate = done / elapsed * 3600 if elapsed > 0 else 0.0
        typer.echo(
            f"[{elapsed / 60:7.1f} min] year={snapshot['year']} done={done} "
            f"(new={snapshot['site_years_completed']} cached={snapshot['cached']} "
            f"no_data={snapshot['no_data']} failed={snapshot['failed']} "
            f"skipped={snapshot['skipped']}) requests={snapshot['requests']} "
            f"rows={snapshot['rows']} rate={rate:.0f}/h",
            err=True,
        )

    typer.echo(
        download_training(
            _config(config),
            refresh=refresh,
            resume=not no_resume,
            cache_only=cache_only,
            site_limit=sites_limit,
            site_offset=site_offset,
            years=chosen_years,
            batch_sites=batch_sites,
            min_free_disk_gb=min_free_disk_gb,
            prune_new_verified_raw=True if prune_new_raw else None,
            on_progress=report,
        )
    )


@training_audit_app.command("smoke")
def training_smoke_command(
    config: Path = typer.Option(Path("configs/training_audit.yaml")),
    cache_dir: Path = typer.Option(Path(".cache/training_audit_smoke")),
    year: int | None = typer.Option(None),
    sites: int = typer.Option(5, help="Number of smoke sites."),
    force_network: bool = typer.Option(
        False, help="Ignore cache and require at least one real request."
    ),
) -> None:
    """Run an isolated real-network smoke test without touching the main cache."""
    typer.echo(
        run_network_smoke(
            _config(config),
            cache_dir=cache_dir,
            force_network=force_network,
            year=year,
            n_sites=sites,
            batch_sites=sites,
        )
    )


@training_audit_app.command("hourly")
def training_hourly_command(
    config: Path = typer.Option(Path("configs/training_audit.yaml")),
    refresh: bool = typer.Option(False),
    sites_limit: int | None = typer.Option(None),
    workers: int = typer.Option(1, help="Parallel per-station aggregation processes."),
) -> None:
    """Create per-station UTC-hourly values and explicit quality masks."""
    started = time.monotonic()

    def report(snapshot: dict) -> None:
        elapsed = time.monotonic() - started
        rate = snapshot["written"] / elapsed * 3600 if elapsed > 0 else 0.0
        typer.echo(
            f"[{elapsed / 60:7.1f} min] hourly written={snapshot['written']} "
            f"skipped={snapshot['skipped']} last={snapshot['site_no']} rate={rate:.0f}/h",
            err=True,
        )

    typer.echo(
        build_hourly(
            _config(config),
            site_limit=sites_limit,
            refresh=refresh,
            workers=workers,
            on_progress=report,
        )
    )


@training_audit_app.command("report")
def training_report_command(
    config: Path = typer.Option(Path("configs/training_audit.yaml")),
) -> None:
    """Create availability, funnel, sensitivity, manifest and audit reports."""
    typer.echo(build_training_audit_report(_config(config)))


@app.command("catalog")
def catalog_command(
    config: Path = typer.Option(Path("configs/round1.yaml"), help="Round configuration."),
    refresh: bool = typer.Option(False, help="Re-download site catalogs."),
) -> None:
    """Build the candidate catalog and join GAGES-II attributes."""
    cfg = _config(config)
    frame = build_candidates(cfg, refresh_catalog=refresh)
    typer.echo(f"catalog sites: {len(frame)} | candidate_ok: {int(frame['candidate_ok'].sum())}")
    typer.echo(frame[frame["candidate_ok"]]["stratum"].value_counts().to_string())


@app.command("download")
def download_command(
    config: Path = typer.Option(Path("configs/round1.yaml")),
    refresh: bool = typer.Option(False, help="Re-download existing stations."),
    sites_limit: int | None = typer.Option(None, help="Limit number of stations (testing)."),
) -> None:
    """Download paired instantaneous Q and SC records."""
    cfg = _config(config)
    summary = download_all(cfg, refresh=refresh, site_limit=sites_limit)
    typer.echo(summary)


@app.command("qc")
def qc_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Assess cadence, completeness and sensor sanity per station-year."""
    cfg = _config(config)
    typer.echo(run_qc(cfg))


@app.command("events")
def events_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Extract Q-only runoff events and screen SC completeness."""
    cfg = _config(config)
    typer.echo(run_events(cfg))


@app.command("select")
def select_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Select the stratified discovery/validation set."""
    cfg = _config(config)
    selection = run_selection(cfg)
    typer.echo(selection.groupby(["stratum", "split"]).size().to_string())
    typer.echo(f"selected: {len(selection)}")


@app.command("validate")
def validate_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Check the selection against the preregistered independence rules."""
    cfg = _config(config)
    typer.echo(validate_selection(cfg))


@app.command("hysteresis")
def hysteresis_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Diagnose event-scale SC-Q hysteresis on the discovery split."""
    cfg = _config(config)
    typer.echo(run_hysteresis(cfg))


@app.command("forcing")
def forcing_command(
    config: Path = typer.Option(Path("configs/round1.yaml")),
    source: str = typer.Option("aorc", help="aorc or nldas"),
    years: str = typer.Option("2015-2024", help="Inclusive year range."),
    variables: str = typer.Option("all", help="all or core"),
    stations: str = typer.Option("a_cohort", help="a_cohort or all"),
    workers: int = typer.Option(3, help="Concurrent station extractions."),
    out_name: str = typer.Option("aorc", help="Output directory name."),
) -> None:
    """Prepare hourly meteorological forcing for the A-class pilot."""
    cfg = _config(config)
    start, end = (int(part) for part in years.split("-"))
    year_list = list(range(start, end + 1))
    events = a_class_events(cfg)
    events_path = cfg.results_dir / "a_class_events.csv"
    events.to_csv(events_path, index=False)
    typer.echo(
        f"A-class events: {len(events)} ({events['site_no'].nunique()} stations) -> {events_path}"
    )
    if source == "aorc":
        chosen = AORC_VARIABLES if variables == "all" else CORE_VARIABLES
        summary = extract_aorc(
            cfg,
            years=year_list,
            variables=chosen,
            stations=stations,
            workers=workers,
            out_name=out_name,
        )
        typer.echo(f"AORC stations: {summary['n_stations']}")
    else:
        extract_nldas(cfg)


@app.command("census")
def census_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Run the full Tier-1 census: all candidate stations and A/B/C/X classes."""
    cfg = _config(config)
    typer.echo(run_census(cfg))


@app.command("figures")
def figures_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Render the screening figures."""
    cfg = _config(config)
    typer.echo(make_figures(cfg))


@app.command("report")
def report_command(config: Path = typer.Option(Path("configs/round1.yaml"))) -> None:
    """Write the human-readable RESULTS.md record."""
    cfg = _config(config)
    typer.echo(write_report(cfg))


@app.command("run")
def run_command(
    config: Path = typer.Option(Path("configs/round1.yaml")),
    refresh_catalog: bool = typer.Option(False),
    refresh_download: bool = typer.Option(False),
    skip_download: bool = typer.Option(False),
    sites_limit: int | None = typer.Option(None),
) -> None:
    """Run catalog, download, QC, events, selection and figures."""
    cfg = _config(config)
    manifest = run_pipeline(
        cfg,
        refresh_catalog=refresh_catalog,
        refresh_download=refresh_download,
        skip_download=skip_download,
        sites_limit=sites_limit,
    )
    typer.echo(
        f"selected {manifest['selection']['n_selected']} stations "
        f"({manifest['selection']['n_discovery']} discovery / "
        f"{manifest['selection']['n_validation']} validation)"
    )
    typer.echo(f"run record: {cfg.experiment_dir / 'run.json'}")


if __name__ == "__main__":
    app()
