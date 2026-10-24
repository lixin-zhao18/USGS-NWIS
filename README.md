# USGS-NWIS

Reproducible screening and event-scale hysteresis analysis of paired USGS streamflow (`00060`) and specific conductance (`00095`) observations.

## Current experiment

`experiments/round1/` screens 40 catchments before any AI model is developed:

1. verify real Q–SC temporal overlap;
2. apply independent data-quality and catchment filters;
3. extract runoff events using Q only;
4. select 40 stations without using hysteresis metrics;
5. diagnose SC–Q hysteresis on a 30-station discovery subset while retaining 10 stations for later validation.

Large raw and intermediate time series are intentionally not committed. Each experiment records code/config versions, source requests, checksums, selected stations, summaries, and figures.

## Quick start

```powershell
uv sync
uv run pytest
uv run usgs-nwis --help
```

Run the whole first round:

```powershell
uv run usgs-nwis run
```

Stages can be resumed separately (downloads are skipped when a station file
already exists):

```powershell
uv run usgs-nwis catalog          # 49 state catalogs + GAGES-II join
uv run usgs-nwis download         # ~114 candidate stations, 2015-2024
uv run usgs-nwis qc               # cadence, paired completeness, sensor sanity
uv run usgs-nwis events           # Q-only events, SC completeness screen
uv run usgs-nwis select           # stratified 40-station selection
uv run usgs-nwis hysteresis       # discovery-only SC-Q loop diagnostics
uv run usgs-nwis figures
uv run usgs-nwis report
```

`run.json` records the config hash, git revision, request provenance and
checksums of every committed output. See
[`experiments/round1/README.md`](experiments/round1/README.md) for the
preregistered protocol and
[`experiments/round1/RESULTS.md`](experiments/round1/RESULTS.md) for the
generated screening record.

## Reproducibility notes

- Bulk observations come from the legacy multi-site instantaneous-values
  service, which remains supported until Q1 2027. The modern OGC API is rate
  limited to 1,000 requests per hour per IP and is not used for bulk values.
- All timestamps are converted to UTC from the explicit offsets returned by the
  service.
- Catchment area, climate, land cover and dam density come from GAGES-II
  (Falcone 2011, DOI 10.5066/P96CPHOT).
- Raw and intermediate observations stay local (`data/raw`, `data/interim`,
  `data/processed`); the committed record contains the selection, QC/event
  summaries, request provenance and figures.
