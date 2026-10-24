"""Command-line entry points."""

from __future__ import annotations

from pathlib import Path

import typer

from .catalog import build_candidates
from .config import load_config
from .download import download_all
from .events import run_events
from .figures import make_figures
from .hysteresis import run_hysteresis
from .pipeline import run_pipeline
from .qc import run_qc
from .report import write_report
from .selection import run_selection, validate_selection

app = typer.Typer(add_completion=False, help="Reproducible USGS Q–SC catchment screening.")


def _config(path: Path):
    return load_config(path)


@app.command("catalog")
def catalog_command(
    config: Path = typer.Option(Path("configs/round1.yaml"), help="Round configuration."),
    refresh: bool = typer.Option(False, help="Re-download site catalogs."),
) -> None:
    """Build the candidate catalog and join GAGES-II attributes."""
    cfg = _config(config)
    frame = build_candidates(cfg, refresh_catalog=refresh)
    typer.echo(
        f"catalog sites: {len(frame)} | candidate_ok: {int(frame['candidate_ok'].sum())}"
    )
    typer.echo(
        frame[frame["candidate_ok"]]["stratum"].value_counts().to_string()
    )


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
