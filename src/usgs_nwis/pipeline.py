"""End-to-end orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .catalog import build_candidates
from .download import download_all
from .events import run_events
from .figures import make_figures
from .io import (
    ensure_dir,
    git_revision,
    runtime_info,
    sha256_file,
    utc_now,
    write_json,
)
from .qc import run_qc
from .report import write_report
from .selection import run_selection, validate_selection


def _outputs(cfg) -> list[Path]:
    paths = [
        cfg.experiment_dir / "selection.csv",
        cfg.experiment_dir / "RESULTS.md",
        cfg.results_dir / "candidate_reconciliation.csv",
        cfg.results_dir / "qc_summary.csv",
        cfg.results_dir / "qc_station_summary.csv",
        cfg.results_dir / "event_station_summary.csv",
        cfg.results_dir / "exclusion_summary.csv",
        cfg.results_dir / "selection_summary.json",
        cfg.results_dir / "selection_checks.json",
        cfg.results_dir / "data_provenance.csv",
    ]
    paths.extend(sorted(cfg.figures_dir.glob("*.pdf")))
    return [path for path in paths if path.exists()]


def write_provenance(cfg) -> Path:
    rows: list[dict] = []
    catalog_manifest = cfg.data_dir / "raw" / "catalog" / "manifest.jsonl"
    if catalog_manifest.exists():
        for line in catalog_manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            rows.append(
                {
                    "source": "site_catalog",
                    "selector": record.get("state"),
                    "start": None,
                    "end": None,
                    "url": record.get("url"),
                    "retrieved_at": record.get("retrieved_at"),
                    "sha256": record.get("sha256"),
                    "n_rows": None,
                    "n_series": None,
                    "missing_sites": None,
                }
            )
    iv_manifest = cfg.data_dir / "raw" / "iv" / "manifest.jsonl"
    if iv_manifest.exists():
        for line in iv_manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            rows.append(
                {
                    "source": "iv_batch",
                    "selector": ",".join(record.get("sites", [])),
                    "start": record.get("start"),
                    "end": record.get("end"),
                    "url": record.get("url"),
                    "retrieved_at": record.get("retrieved_at"),
                    "sha256": record.get("sha256"),
                    "n_rows": record.get("n_rows"),
                    "n_series": record.get("n_series"),
                    "missing_sites": ",".join(record.get("missing_sites", [])),
                }
            )
    frame = pd.DataFrame(rows)
    output = cfg.results_dir / "data_provenance.csv"
    frame.to_csv(output, index=False)
    return output


def run_pipeline(
    cfg,
    *,
    refresh_catalog: bool = False,
    refresh_download: bool = False,
    skip_download: bool = False,
    sites_limit: int | None = None,
) -> dict:
    started = utc_now()
    ensure_dir(cfg.results_dir)
    ensure_dir(cfg.figures_dir)

    (_, candidates) = _catalog(cfg, refresh_catalog)
    download_summary = {"skipped": True}
    if not skip_download:
        download_summary = download_all(
            cfg, refresh=refresh_download, site_limit=sites_limit
        )
    else:
        station_dir = cfg.data_dir / "interim" / "stations"
        manifest = cfg.data_dir / "raw" / "iv" / "manifest.jsonl"
        requests = 0
        rows = 0
        if manifest.exists():
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                requests += 1
                rows += int(record.get("n_rows") or 0)
        download_summary = {
            "skipped": True,
            "stations_on_disk": len(list(station_dir.glob("*.parquet"))),
            "requests_in_manifest": requests,
            "rows_in_manifest": rows,
        }
    qc_summary = run_qc(cfg)
    event_summary = run_events(cfg)
    selection = run_selection(cfg)
    checks = validate_selection(cfg)
    make_figures(cfg)
    write_report(cfg)
    write_provenance(cfg)

    manifest = {
        "experiment_id": cfg.experiment_id,
        "started_at": started,
        "finished_at": utc_now(),
        "config": {
            "path": cfg.path.as_posix(),
            "sha256": sha256_file(cfg.path),
        },
        "git": git_revision(cfg.root),
        "runtime": runtime_info(),
        "catalog": {
            "n_catalog_sites": int(len(candidates)),
            "n_candidate_ok": int(candidates["candidate_ok"].sum()),
        },
        "download": download_summary,
        "qc": qc_summary,
        "events": event_summary,
        "selection": {
            "checks_ok": bool(checks["ok"]),
            "n_selected": int(len(selection)),
            "n_discovery": int((selection["split"] == "discovery").sum()),
            "n_validation": int((selection["split"] == "validation").sum()),
            "by_stratum": selection["stratum"].value_counts().to_dict(),
        },
        "outputs": {
            path.relative_to(cfg.root).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in _outputs(cfg)
        },
    }
    write_json(cfg.experiment_dir / "run.json", manifest)
    return manifest


def _catalog(cfg, refresh: bool):
    frame = build_candidates(cfg, refresh_catalog=refresh)
    return frame, frame
