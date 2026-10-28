# National Q–SC screening report

Generated: 2026-10-28T08:53:04+00:00
Config SHA256: `8df1497a5043f650b363dde7e69eac139646d215d4524e64d3c25cb376648322`
Commit: `39443b24931f2328dccb28961f746b45625b3e64`

## Scope

CONUS states configured; 2015-2024; IV Q=00060 and SC=00095. Records come from the legacy instantaneous-values service,
`00060` (discharge, ft3/s) and `00095` (specific conductance, uS/cm at 25 C),
converted to UTC and aggregated to hourly means without interpolation. The
frozen candidate pool is the catalog `window_overlap == true` layer, which
contains 1122 stations and was not
re-filtered in this stage.

## Screening funnel

| step | relative_to | station_count | dropped_from_parent | drop_reasons_json |
| --- | --- | --- | --- | --- |
| catalog_union |  | 13767 | 0 | [] |
| catalog_paired | catalog_union | 1266 | 12501 | [["not_catalog_paired", 12501]] |
| window_overlap | catalog_paired | 1122 | 144 | [["no_catalog_overlap_in_window", 144]] |
| not_tidal_or_ocean | window_overlap | 1111 | 11 | [["tidal_or_ocean_influenced", 11]] |
| downloaded | not_tidal_or_ocean | 1111 | 0 | [] |
| has_paired_hours | downloaded | 1075 | 36 | [["no_paired_valid_hour", 36]] |
| has_valid_year | has_paired_hours | 843 | 232 | [["no_valid_year", 232]] |
| extended_eligible | has_valid_year | 385 | 460 | [["train_valid_years", 398], ["train_paired_hours", 218], ["validation_valid_years", 215], ["test_valid_years", 139]] |
| core_eligible | extended_eligible | 322 | 63 | [["train_years_q_coverage", 35], ["test_years_q_coverage", 34], ["validation_years_q_coverage", 29], ["test_valid_years", 21], ["falling_phase_coverage", 4], ["low_state_coverage", 3], ["mid_state_coverage", 1]] |

## Download status

| download_status | stations |
| --- | --- |
| not_attempted | 12645 |
| downloaded | 1121 |
| no_data | 1 |

Site-year outcomes recorded in `download_manifest.csv`:

```json
{
  "no_data": 1387,
  "success": 9833
}
```

Last download run: requests=9,
rows=4496088, years=[2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
sites selected=1122.

## Training tiers

| tier | stations |
| --- | --- |
| T0_catalog_only | 12682 |
| T1_hourly_overlap | 700 |
| T3_hourly_core | 322 |
| T2_hourly_extended | 63 |

## Core and extended sets

- Core, used for the main conclusions: 322 stations.
- Extended, for larger joint training and robustness: 385 stations.
- Every threshold is the one in `configs/training_audit.yaml`; no threshold was
  changed after the national run.

## SC anomaly flags (diagnostic only)

```json
{
  "sc_constant_gt_30d": 4,
  "sc_isolated_spike": 104,
  "sc_interannual_median_shift": 145,
  "sc_long_term_monotonic_change": 1,
  "sc_instrument_or_range_switch": 73
}
```

An anomaly flag never removes a station or a value. Only missing or
non-numeric values, `SC <= 0`, provisional (`P`) or explicitly invalid
qualifiers, and unresolvable duplicate timestamps are marked invalid.

## Read together with

- `SCREENING_PROTOCOL.md`: fixed rules and reproduction commands.
- `station_availability_national.csv`: one row per catalog station with
  download status, yearly Q/SC/paired hours, split statistics, Q coverage,
  flow-state and phase coverage, anomaly flags and every failed criterion.
- `core_stations.csv`, `extended_stations.csv`, `screening_funnel.csv`.
- `download_manifest.csv`, `failed_requests.csv`.

## Limitations

- no basin boundary polygons were used; boundary and nesting flags are HUC8 proxies
- no point-source or road-salt datasets were joined; both are reported as unavailable
- tidal/ocean exclusion uses station-name keywords only
- raw IV Parquet and national hourly Parquet are not committed
