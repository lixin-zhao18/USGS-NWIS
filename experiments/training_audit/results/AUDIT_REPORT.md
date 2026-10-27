# Training-data audit report

Generated: 2026-10-27T08:53:47+00:00

This audit starts from the full configured state catalogs. It does **not** use
area, GAGES-II membership, disturbance, three complete years, 80% paired
coverage, or hysteresis geometry as pre-catalog exclusion rules.

## Audit funnel

| layer | relative_to | station_count | dropped_from_parent | drop_reasons_json |
| --- | --- | --- | --- | --- |
| catalog_q | catalog_union | 13193 | 572 | {"catalog_q_missing": 572, "reliable_area_or_attributes_missing": 556} |
| catalog_sc | catalog_union | 1837 | 11928 | {"catalog_sc_missing": 11928, "reliable_area_or_attributes_missing": 5316} |
| catalog_paired | catalog_union | 1265 | 12500 | {"catalog_q_missing": 572, "catalog_sc_missing": 11928, "reliable_area_or_attributes_missing": 5872} |
| window_overlap | catalog_paired | 1122 | 143 | {"no_catalog_overlap_in_window": 143, "reliable_area_or_attributes_missing": 76} |
| actual_overlap | window_overlap | 4 | 1118 | {"not_downloaded_or_no_actual_paired_hour": 1118, "reliable_area_or_attributes_missing": 504} |
| hourly_usable | actual_overlap | 4 | 0 | {} |
| forcing_ready | hourly_usable | 2 | 2 | {"reliable_area_or_attributes_missing": 2} |
| diagnostic_quality | actual_overlap | 0 | 4 | {"reliable_area_or_attributes_missing": 2} |

`window_overlap` is catalog evidence only. `actual_overlap` requires at least
one paired valid UTC hour from downloaded observations. Therefore incomplete
remote execution lowers the latter count and is not interpreted as absence.
The committed smoke-run scope was 5 sites; years [2024].

## Training tiers

| tier | stations |
| --- | --- |
| T0_catalog_only | 13761 |
| T2_hourly_partial | 4 |

## Relationship to the earlier cohorts

- Earlier 114-station candidate members present in the new catalog: 114.
- Earlier event-data-sufficient diagnostic members present: 35.
- These memberships are annotations, never the new population ceiling.

## Current limitations

- actual-overlap and hourly tiers only reflect station files downloaded locally
- basin boundaries and nationwide forcings were not downloaded by this stage
- same-HUC flags do not prove nested catchments or correlation

Approved observations define the primary masks. Provisional observations are
retained in sensitivity columns. No cross-hour interpolation or forward fill
is performed.
