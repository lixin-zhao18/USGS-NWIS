# Training-audit data dictionary

## Scope and storage

The audit covers configured CONUS state catalogs, USGS river sites (`siteType=ST`),
instantaneous values (`iv`/catalog `uv`), streamflow `00060`, and specific
conductance `00095`, during 2015–2024. Raw and per-station hourly files remain
under `data/` and are ignored by Git. Compact tables and provenance are stored
under `experiments/training_audit/results/`.

## Hourly station files

Path: `data/processed/training_audit/hourly/{site_no}.parquet`.

- `time`: start of the UTC aggregation hour.
- `q_value`, `sc_value`: mean of approved, in-range native observations in the hour.
- `q_median`, `sc_median`, `*_std`, `*_min`, `*_max`: approved-observation diagnostics.
- `*_native_samples`: all raw records in the hour, including failed-quality records.
- `*_unique_samples`, `*_duplicate_timestamps`: explicit duplicate accounting.
- `*_approved_samples`, `*_sensitivity_samples`: samples admitted by the primary
  and sensitivity qualifier rules.
- `*_expected_samples`: expected count derived from that parameter's modal native
  interval for the station-year.
- `*_hour_coverage`, `*_sensitivity_coverage`: admitted unique-time sample count
  divided by expected sample count, capped at one.
- `*_quality_flags`: sorted union of original USGS qualifier strings in the hour.
- `q_valid`, `sc_valid`, `paired_valid`: approved-primary masks after range and
  configured hourly coverage checks.
- `*_valid_sensitivity`, `paired_valid_sensitivity`: configured nonapproved
  sensitivity masks (currently provisional `P` in addition to approved `A`).
- `missing_or_failure_reason`: all-variable hourly failure category.
- `was_aggregated`: more than one native Q or SC record contributed to the hour.
- `was_interpolated`, `was_forward_filled`: always false in this pipeline.
- `processing`: human-readable aggregation rule.

No value is carried across an hour boundary. Missing hours remain missing; a
complete UTC hourly index makes gaps explicit.

## Station availability

`station_availability.csv/.parquet` contains the catalog union, so Q-only and
SC-only stations remain visible. Major groups are:

- identity/location: `site_no`, name, state, coordinates, HUC2/HUC8;
- catalog evidence: Q/SC flags, record counts, dates, overlap dates/duration;
- attributes/impact: GAGES-II match, area, reference class, dams, development,
  keyword flags (the latter are warnings, never proof of impact);
- actual data: observed dates, valid-hour counts, annual/seasonal JSON summaries,
  native cadence, gaps, duplicate and quality distributions;
- state coverage: Q quantiles, low/mid/high-flow and rising/falling/stable SC
  coverage; wet/dry labels use station-Q thirds unless external drought data exist;
- forcing audit: geographic product coverage, local-file existence, method and
  missing reason. Geographic coverage is never called downloaded/ready data;
- traceability: all failed conditions, old 114/diagnostic cohort membership,
  tier, and spatial-dependence flags.

## Audit layers and tiers

`catalog_q`, `catalog_sc`, `catalog_paired`, and `window_overlap` are catalog
evidence. `actual_overlap` requires real paired valid hours. `hourly_usable` is
the configured partial-supervision threshold. `forcing_ready` means attributes,
coordinates and product extraction conditions can be matched; it does not mean
forcing files were downloaded. `diagnostic_quality` is an independent process-
diagnostic annotation.

Training tiers are `T0_catalog_only`, `T1_overlap`, `T2_hourly_partial`,
`T3_hourly_core`, and `T4_diagnostic`. Thresholds live only in
`configs/training_audit.yaml`.
