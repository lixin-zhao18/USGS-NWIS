# Reproducing the training-data audit

Install the project and development dependencies:

```powershell
uv sync --extra dev
```

Run the stages (each stage reuses complete cache files unless `--refresh` is
given):

```powershell
uv run usgs-nwis training-audit catalog
uv run usgs-nwis training-audit download
uv run usgs-nwis training-audit hourly
uv run usgs-nwis training-audit report
```

Small real-data smoke run:

```powershell
uv run usgs-nwis training-audit download --sites-limit 1 --years 2024-2024
uv run usgs-nwis training-audit hourly --sites-limit 1
uv run usgs-nwis training-audit report
```

Catalog and raw request checksums, request URLs, retrieval times, missing sites,
errors and retries are recorded under `data/raw/catalog/` and
`experiments/training_audit/results/`. A `.complete.json` marker per site-year
prevents successful empty responses from being re-requested. Remove only the
specific marker to retry that site-year.

Large local-only paths:

- `data/raw/training_audit/iv/`
- `data/processed/training_audit/hourly/`
- `data/raw/forcing/`

The committed `hourly_files_manifest.csv` records each local hourly file's size,
row count, time span, valid-hour counts, SHA256, generation time, configuration
hash and Git revision.
