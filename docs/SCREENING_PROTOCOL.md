# Screening protocol: nationwide Q–SC training data

Frozen before the national run. Thresholds live in `configs/training_audit.yaml`
(`screening:` block) and are not adjusted after seeing results. Round-1 rules
(`quality:`, `events:`, `hysteresis:`) keep their original meaning and are not
used to define this population.

## First-stage question

> For stations with SC observations, can SC-assisted supervision improve
> future-period discharge prediction and help the model learn more credible
> hydrologic states?

Everything below is screening for that question only. No model is trained here.

## 1. Population

- Source: USGS legacy instantaneous-values service, `00060` (discharge) and
  `00095` (specific conductance), 2015-01-01 through 2024-12-31, UTC.
- Candidate pool: catalog `window_overlap == true`, i.e. the catalog shows both
  parameters overlapping the ten-year window. This is the frozen 1,122-station
  pool. It is **not** re-filtered by area, GAGES-II membership, disturbance,
  event counts, hysteresis strength, or any SC–Q relationship.
- No NLDAS, AORC, national forcing, or large static attribute product is
  downloaded at this stage.

## 2. Download

- One request per site × year, batched at up to 10 sites per HTTP call with
  bounded concurrency; retries use exponential backoff (5 s, 15 s, 45 s).
- A failed site-year is recorded in `failed_requests.csv` and never stops the
  run. Failures are retried by rerunning the same command at the end.
- Statuses per site-year: `success`, `cached`, `no_data`, `failed`, `skipped`.
- `download_manifest.csv` is updated after every batch and is the resume
  authority, together with `{site}/{year}.complete.json` markers written only
  after the raw Parquet has been re-read and row-verified.
- Disk guard: before each batch, free space must stay above
  `min_free_disk_gb` (20 GB). If it does not, the run stops cleanly, records
  `skipped` rows with the guard message, and resumes from the same command.
- Raw IV Parquet is kept compressed by default. `prune_new_verified_raw` may
  only delete newly downloaded raw files whose hourly output has been verified;
  it never touches pre-existing raw data or other user files.

## 3. Quality marks

Values are classified from **all** raw qualifier tokens, never substrings:

- `A` → approved; accepted.
- `A` with `e` (estimated) → accepted, and counted in `estimated_mask`.
- Any token `P` (provisional) → excluded. A value carrying both `A` and `P` is
  excluded; the legacy `accepted_nonapproved_qualifiers: [P]` rule is not used.
- Explicit invalid flags (`ICE`, `EQP`, `Mnt`, `Dis`, …) are not approved.
- `SC <= 0` is invalid. Values outside the configured physical ranges are
  invalid. Non-numeric or missing values are invalid.
- Duplicate timestamps that cannot be resolved are invalid.
- All raw qualifier strings are preserved in `q_quality_flags` /
  `sc_quality_flags`.
- No interpolation, no forward fill, no gap filling of any kind.

## 4. Hourly aggregation

- Native observations are converted to UTC from their explicit offsets, then
  grouped into UTC hours.
- Native cadence is inferred per station, parameter and unbroken segment
  (`*_native_cadence_minutes`); expected samples per hour follow from it.
- An hourly mean is computed only when approved **unique-timestamp** coverage is
  at least 75% of the expected native samples (`hourly_min_unique_timestamp_coverage`).
  The station-year is not otherwise dropped.
- Paired hours exist only where both `q_valid` and `sc_valid` are true.
- Output columns include `q_valid`, `sc_valid`, `paired_valid`, native counts,
  approved/estimated/rejected sample counts, native coverage, and
  `was_interpolated` / `was_forward_filled` (both always false).

## 5. Fixed splits

| split | window |
| --- | --- |
| train | 2015-01-01 – 2019-12-31 |
| validation | 2020-01-01 – 2021-12-31 |
| test | 2022-01-01 – 2024-12-31 |

By calendar year:

- cold season = January–March + December; warm season = April–November.
- A valid day has at least 12 valid Q–SC paired hours.
- A valid year has at least 60 valid cold days **and** at least 60 valid warm days.
- Q coverage of a year = valid Q hours / expected hours in that year.

## 6. Core tier (main conclusions)

Training period (2015–2019):

- at least 3 valid years;
- at least 10,000 paired hours;
- at least 3 years with Q coverage ≥ 80%.

Validation (2020–2021): at least 1 valid year, at least 1 year with Q coverage ≥ 70%.

Test (2022–2024): at least 2 valid years, at least 2 years with Q coverage ≥ 70%.

Flow state: 30th/70th percentile of training-period valid Q define low/mid/high.
Each of low, mid, high must contain ≥ 200 paired hours and ≥ 10% of all paired
hours. Phase: Q smoothed with a centred 3-hour median; rising and falling each
need ≥ 200 paired hours.

The station is never filtered on SC–Q correlation, hysteresis direction, or
hysteresis strength.

## 7. Extended tier

- training: ≥ 3 valid years and ≥ 5,000 paired hours;
- validation: ≥ 1 valid year; test: ≥ 1 valid year;
- all other quality rules unchanged.

Core supports the main conclusions; Extended supports larger joint training and
robustness checks. Thresholds are not changed after the run.

## 8. SC anomaly flags (diagnostic only)

Automatically invalid: missing/non-numeric, `SC <= 0`, provisional or explicitly
invalid qualifiers, unresolvable duplicate timestamps. Nothing else.

Flagged, never auto-deleted:

| flag | rule |
| --- | --- |
| `sc_constant_gt_30d` | identical valid value for ≥ 720 consecutive hours |
| `sc_isolated_spike` | ≥ 3 excursions ≥ 500 µS/cm from the local 7-hour median that recover within 3 hours |
| `sc_interannual_median_shift` | ratio > 1.5 between annual medians of years with ≥ 720 valid hours |
| `sc_long_term_monotonic_change` | ≥ 6 usable years, ≥ 90% consistent sign of annual-median steps, ≥ 50% relative change |
| `sc_instrument_or_range_switch` | native cadence changes within the record |

"Spike > 500 µS/cm" is never a deletion rule, and no drift is inferred from a
trend alone.

## 9. Basin conditions

- Hard exclusion: tidal/ocean-influenced stations, identified by station-name
  keywords (`TIDAL`, `ESTUARY`, `BAY`, `SOUND`, `INLET`, `OCEAN`, `GULF`,
  `BRACKISH`, `SEAWATER`). No estuary polygon test is available at this stage;
  this limitation is recorded in every output.
- Flags only: missing basin boundary, missing drainage area, reservoir
  regulation (`NDAMS_2009 ≥ 1`), urbanization (`DEVNLCD06 ≥ 20`), human-impact
  name keywords, same-HUC8 nesting, and point-source / road-salt data
  unavailability.
- No 10–1,000 km² hard limit, no leave-one-station-out, no HUC2 holdout, no
  climate-zone spatial validation. The sample is not shrunk to protect future
  no-SC basins.

## 10. Outputs

`experiments/training_audit/results/`:

- `station_availability_national.csv` / `.parquet` — one row per catalog
  station: identity, catalog layer, download status, yearly Q/SC/paired hours,
  split statistics, Q coverage, low/mid/high and rising/falling coverage,
  estimated ratios, SC anomaly flags, basin flags, tier eligibility and every
  failed criterion.
- `core_stations.csv`, `extended_stations.csv`
- `screening_funnel.csv` — stations at each step and drop reasons.
- `download_manifest.csv`, `failed_requests.csv`
- `NATIONAL_SCREENING_REPORT.md`, `SCREENING_PROTOCOL.md` (this file)
- `audit_summary.json`

Hourly Parquet and raw IV Parquet stay under `data/` and are Git-ignored.

## 11. Reproduce and resume

```bash
uv sync --extra dev

# 1. frozen candidate pool (only if catalog.parquet is missing)
uv run usgs-nwis training-audit catalog

# 2. real-network smoke test in an isolated cache
uv run usgs-nwis training-audit smoke --force-network --sites 5

# 3. national download, resumable, batch of 10 sites per request
uv run usgs-nwis training-audit download --batch-sites 10 --min-free-disk-gb 20

# 4. UTC hourly aggregation, masks and coverage
uv run usgs-nwis training-audit hourly

# 5. national tables, tiers, funnel and report
uv run usgs-nwis training-audit report
```

Every command is idempotent. Rerunning `download` reuses verified markers
(`cached`), retries recorded failures, and stops again only if free disk falls
below the guard. `hourly` skips stations whose hourly file already exists
unless `--refresh` is passed.

## 12. Interpretation limits

- `window_overlap` is catalog evidence, not observed data.
- Station counts are sensitive to USGS service outages; those station-years are
  `failed`, not absent.
- Name-based tidal exclusion and HUC8 nesting flags are proxies for boundary
  analysis that this stage does not attempt.
