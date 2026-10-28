# Reproducing the training-data audit

Install the project and development dependencies:

```powershell
uv sync --extra dev
```

## Full national run

Each stage is idempotent and resumes from what is already on disk.

```powershell
# 1. Catalog: build the unfiltered population and the frozen window_overlap pool
uv run usgs-nwis training-audit catalog

# 2. Isolated real-network smoke test (no effect on the national cache)
uv run usgs-nwis training-audit smoke --force-network --sites 5

# 3. National download: 1,122 stations x 2015-2024, one request per site-year
#    batched 10 sites per call, bounded concurrency, disk guard at 20 GB
uv run usgs-nwis training-audit download --batch-sites 10 --min-free-disk-gb 20

# 4. Re-run the same command to retry recorded failures; completed site-years
#    are reused from their verified markers.

# 5. UTC hourly aggregation, masks and coverage (parallel per station)
uv run usgs-nwis training-audit hourly --workers 8

# 6. National availability table, Core/Extended lists, funnel and report
uv run usgs-nwis training-audit report
```

Estimated cost on a 16-core / 32 GB machine: catalog minutes, smoke under two
minutes, download about 5 hours, hourly aggregation about 15 minutes, report
about 15 minutes.

## Resuming after an interruption

- Rerun the exact same `download` command. Site-years with a verified
  `{site}/{year}.complete.json` marker are reported as `cached` and are not
  re-requested; recorded failures are retried; a disk guard below
  `min_free_disk_gb` stops the run cleanly with `skipped` rows.
- Rerun `hourly`: stations with an existing hourly Parquet are skipped.
- Rerun `report`: it only reads local hourly files and the manifest.

Small single-station check:

```powershell
uv run usgs-nwis training-audit download --sites-limit 1 --years 2024-2024
uv run usgs-nwis training-audit hourly --sites-limit 1
```

## Provenance and checksums

Catalog and raw request checksums, request URLs, retrieval times, missing sites,
errors and retries are recorded under `data/raw/catalog/` and
`experiments/training_audit/results/`. A `.complete.json` marker per site-year
prevents successful empty responses from being re-requested; remove only that
marker to force a retry. The national plan, thresholds and interpretation limits
are in `docs/SCREENING_PROTOCOL.md`.

Large local-only paths (never committed):

- `data/raw/training_audit/iv/`
- `data/processed/training_audit/hourly/`
- `data/raw/forcing/`

The committed `hourly_files_manifest.csv` records each local hourly file's size,
row count, time span, valid-hour counts, SHA256, generation time, configuration
hash and Git revision.
