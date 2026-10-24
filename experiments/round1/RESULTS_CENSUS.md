# Tier-1 census — all 114 candidate stations

- Generated: 2026-10-24T06:40:27+00:00
- Scope: every `candidate_ok` station (no discovery/validation split); all usable events; geometry classes are assigned before any loop metric.

## Event classes

| Class | Definition | Events | Fraction |
|---|---|---:|---:|
| A_simple_closed | single Q peak, monotonic limbs, one SC excursion, SC recovery ≥50% | 1,882 | 25% |
| B_complex_loop | multi-peak hydrograph, non-monotonic limbs or multi-excursion SC | 1,667 | 22% |
| C_weak_open | SC does not recover, SC minimum misplaced, or SC amplitude too small | 3,923 | 52% |
| X_no_usable_sc | insufficient paired coverage or too few native points | 64 | 1% |

## Stations

- 114 candidate stations; 109 have usable events.
- Stations with ≥20 simple loops: **35**; of those, 31 meet the reproducibility criteria.
- Stations with ≥20 complex loops: 31; with ≥20 weak/open events: 54.

| stratum | stations | simple20 | reproducible |
|---|---|---|---|
| humid_rainfall | 45 | 22 | 20 |
| semiarid_flashy | 6 | 1 | 0 |
| snow_mixed | 63 | 12 | 11 |
- Applying the same HUC8/name independence rule reduces the 35 simple-sufficient stations to 29 independent catchments, and the 31 reproducible stations to 25 independent catchments.

## Simple-loop signal

- 1,882 simple loops; 1,522 clockwise, 322 counterclockwise, 38 ambiguous.
- Median normalized loop area 0.286 [0.186–0.416].

## Event-size confound

- Simple loops are systematically larger events than complex or weak events (median event size differs by a factor of ~4). Any A-vs-B model comparison must control for event size, for example by matching events within size deciles.

## Interpretation

- The pilot's 60% reproducibility was measured on a quality-selected subset. In the full Tier-1 census the binding constraint is data sufficiency, not direction: only 35/114 stations have ≥20 simple loops, and 31 of those 35 are direction-reproducible.
- The analysis cohort should therefore be the 35 data-sufficient stations (or the 31 reproducible ones), while the dataset keeps all 114 with class flags.
- Weak/open events dominate the record (52%). They are not failures of the dataset; they are the negative/weak-hysteresis class and should be reported.
- Semiarid remains empty: no semiarid station has ≥20 simple loops.
