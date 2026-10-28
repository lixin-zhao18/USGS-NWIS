# Training-audit data dictionary

## Scope and storage

The audit covers configured CONUS state catalogs, USGS river sites (`siteType=ST`),
instantaneous values (`iv`/catalog `uv`), streamflow `00060`, and specific
conductance `00095`, during 2015–2024. The frozen candidate pool is the catalog
`window_overlap == true` layer. Raw and per-station hourly files remain under
`data/` and are ignored by Git. Compact tables, manifests and provenance are
stored under `experiments/training_audit/results/`.

## Hourly station files

Path: `data/processed/training_audit/hourly/{site_no}.parquet`.

- `time`: start of the UTC aggregation hour (full 2015–2024 index, so gaps are
  explicit).
- `q_value`, `sc_value`: mean of approved, in-range native observations in the hour.
- `q_median`, `sc_median`, `*_std`, `*_min`, `*_max`: approved-observation diagnostics.
- `*_native_samples`: all raw records in the hour, including failed-quality records.
- `*_unique_samples`, `*_duplicate_timestamps`: explicit duplicate accounting.
- `*_approved_samples`: samples admitted by the primary rule (`A`).
- `*_estimated_samples`: approved samples flagged estimated (`A` with `e`).
- `*_rejected_samples`: samples carrying a rejected token (`P` or invalid flags).
- `*_sensitivity_samples`, `*_valid_sensitivity`, `paired_valid_sensitivity`: kept for
  output compatibility; provisional `P` is rejected everywhere, so these currently
  equal the approved masks.
- `*_expected_samples`: expected count derived from that parameter's inferred native
  cadence for the station-year.
- `*_hour_coverage`: approved unique-time sample count divided by expected sample
  count, capped at one. `*_valid` requires the configured 75% coverage.
- `*_quality_flags`: sorted union of original USGS qualifier strings in the hour;
  the raw qualifiers are never rewritten.
- `q_valid`, `sc_valid`, `paired_valid`: approved-primary masks after range, quality
  and hourly-coverage checks.
- `missing_or_failure_reason`: all-variable hourly failure category.
- `was_aggregated`: more than one native Q or SC record contributed to the hour.
- `was_interpolated`, `was_forward_filled`: always false in this pipeline.
- `processing`: human-readable aggregation rule.

No value is carried across an hour boundary. Missing hours remain missing.

## Station availability

`station_availability_national.csv/.parquet` contains the catalog union, so
Q-only, SC-only and non-pool stations remain visible; `window_overlap` marks the
frozen 1,122-station candidate pool. Major groups are:

- identity/location: `site_no`, name, state, coordinates, HUC2/HUC8;
- catalog evidence: Q/SC flags, record counts, dates, overlap dates/duration;
- attributes/impact: GAGES-II match, area, reference class, dams, development,
  keyword flags (flags only, never proof of impact);
- download status: `download_status`, per-status site-year counts,
  `download_years_with_data`, `download_errors`;
- actual data: observed dates, valid-hour counts, yearly/seasonal JSON summaries,
  native cadence, gaps, duplicate and quality distributions, estimated ratios;
- fixed splits: per-split valid days (total, cold, warm), valid years, paired
  hours, Q coverage and per-year coverage JSON;
- state and phase: training-period 30/70 Q quantiles, low/mid/high paired hours
  and fractions, rising/falling/stable paired hours and fractions;
- SC anomaly flags (diagnostic only): constant run, isolated spike, interannual
  median shift, long-term monotonic change, instrument/range switch;
- basin conditions: `tidal_ocean_excluded` (hard exclusion), plus boundary,
  area, reservoir, urbanization, name, nesting and data-unavailability flags;
- eligibility: `extended_eligible`, `core_eligible` with explicit failed criteria,
  `screening_failed_reasons`, and the training tier.

## Audit layers and tiers

`catalog_q`, `catalog_sc`, `catalog_paired`, and `window_overlap` are catalog
evidence. `has_paired_hours` requires real paired valid hours; `has_valid_year`
requires at least one calendar year meeting the cold and warm valid-day rules.

Training tiers: `T0_catalog_only`, `T1_hourly_overlap`, `T2_hourly_extended`,
`T3_hourly_core`. Thresholds live only in `configs/training_audit.yaml`
(`screening:` block); see `docs/SCREENING_PROTOCOL.md`.

## Tables

- `station_availability_national.csv/.parquet`: one row per catalog station.
- `core_stations.csv`, `extended_stations.csv`: eligible station lists.
- `screening_funnel.csv`: station counts and drop reasons at each step.
- `download_manifest.csv`: one row per site-year with status, rows, hashes, timing.
- `failed_requests.csv`: one row per failed request with its error.
- `hourly_files_manifest.csv`: hourly file sizes, row counts and SHA256.
- `NATIONAL_SCREENING_REPORT.md`, `SCREENING_PROTOCOL.md`, `audit_summary.json`.
