# Round 1 — 40-catchment screening

## Scientific question

Do paired high-frequency streamflow (Q) and specific conductance (SC) observations contain reproducible event-scale hysteresis that is consistent with time-varying source/pathway connectivity and not reducible to a single static SC–Q relation?

## Scope

This round establishes data feasibility. It does not train an AI model and does not claim that SC directly observes a unique connectivity state.

## Frozen rules before hysteresis inspection

- Candidate analysis window: 2015-01-01 through 2024-12-31 UTC.
- Parameters: Q=`00060`, SC=`00095`.
- Median/modal empirical cadence for both variables must be no coarser than 60 min.
- At least three qualifying synchronized calendar years.
- At least 80% paired Q–SC completeness within each qualifying year.
- No long-gap interpolation counts toward completeness.
- Event boundaries use Q only; SC cannot determine station eligibility or event boundaries.
- At least 50 usable runoff events per selected station.
- Prefer 10–200 km² and low-disturbance/reference catchments; exclude tidal and strongly regulated/diverted sites when documented.
- Target strata: 20 humid rainfall-dominated, 10 snow/rain-snow, 10 semiarid/flashy.
- Fixed selection seed: 20261023.
- Discovery/validation split: 30/10. Validation diagnostics remain unopened during this round.

If the fixed criteria yield fewer than 40 stations or leave a stratum undersubscribed, the run reports the shortfall. Criteria are not silently relaxed.

## Amendments made before any hysteresis was inspected (2026-10-24)

These changes were fixed before SC–Q event geometry was computed. They respond to
documented properties of the candidate pool, not to hysteresis outcomes.

1. **Attribute source.** Catchment area, climate, land cover and dam density come
   from GAGES-II (conterminous 9,322 gages). A candidate must match a GAGES-II
   station; this makes area and disturbance definitions consistent but restricts
   the pool to conterminous reference-gage records. Catalog-only NWIS sites are
   counted in the reconciliation table and excluded as `gages2_missing`.
2. **Semiarid stratum.** The original label is retained, but it is operationally
   defined as aridity index (mean annual precipitation / PET) `< 1.0`, i.e. dry
   sub-humid or drier. The USGS continuous-SC network contains almost no true
   semiarid small catchments; only a handful of candidates meet this definition.
   The shortfall is reported and remaining slots are reallocated, never filled by
   lowering the data-quality thresholds.
3. **Disturbance hard limits.** Basins with developed land cover `> 20%` or dam
   density `> 5` dams per 100 km² are excluded. Sites below those limits are not
   hard-filtered; they are ranked by GAGES-II reference status, development,
   dam density, paired completeness and usable event count.
4. **Data service.** Bulk values are taken from the legacy multi-site
   instantaneous-values service (`nwis.waterservices.usgs.gov`), which remains
   supported until Q1 2027 and returns explicit UTC offsets. The modern OGC API
   is used only where useful because it is limited to 1,000 requests per hour per
   IP. All timestamps are converted to UTC.
5. **Event limb points.** Event peaks and boundaries are delineated on hourly Q;
   the rising-limb point count required by `min_limb_points` is then evaluated on
   the native (typically 15-minute) series, because a two-hour rise has only two
   hourly points but eight native points.
6. **Spatial independence.** At most two selected stations per 8-digit HUC,
   and no two selected stations in the same HUC with the same first distinctive
   name token. This blocks nested gauges on one river (four Floyds Fork sites,
   four Neversink sites) while allowing independent tributaries in the same
   HUC (Mud Creek and McElmo Creek).
7. **SC sensor sanity bounds.** The median SC must lie between 10 and
   10,000 µS/cm. The upper bound only guards against order-of-magnitude unit
   errors: saline inland streams in the Paradox Basin legitimately reach
   5,000–7,500 µS/cm, and a 5,000 bound would have discarded a valid semiarid
   candidate.

## Hysteresis loop criteria (frozen before inspection)

The discovery-only diagnostic uses only the 30 discovery stations. A usable
event enters the loop analysis only if it is single-peaked and closed enough to
be interpreted as one loop:

- at least 20 loop-usable events per station;
- rising and falling Q limbs at least 80% monotonic (2% tolerance);
- SC amplitude at least max(2 µS/cm, 5% of the median SC);
- SC recovers to at least 50% of its pre-event level;
- the SC minimum lies between −10% and +50% of event duration around the Q peak;
- no secondary Q peak above 85% of the main peak;
- SC follows one dominant excursion (80% monotonic with 5% tolerance).

A station is called reproducible when its dominant direction fraction is at
least 60%, its median normalized loop area is at least 0.02, and the same
direction dominates both halves of its events. The dataset passes when at least
60% of the 30 discovery stations are reproducible.

## Selection score

Within each stratum, eligible stations are ranked by a fixed score (weights in
`configs/round1.yaml`): GAGES-II reference status, area inside 10–200 km²,
low-disturbance flag, low development, low dam density, median paired
completeness in qualifying years, usable event count, plus a seeded tie-breaker.
No hysteresis metric enters the score.

## Selection independence

`selection.csv` may contain metadata, Q/SC quality metrics, Q-only event counts, stratum, and split. It must not contain loop area, hysteresis index, direction, or any diagnostic derived from the SC–Q event geometry.

## Outputs

- `results/candidate_reconciliation.csv`
- `results/qc_summary.csv`
- `results/qc_station_summary.csv`
- `results/event_station_summary.csv`
- `results/exclusion_summary.csv`
- `results/selection_summary.json`
- `results/selection_checks.json`
- `results/discovery_event_metrics.csv`
- `results/discovery_station_summary.csv`
- `results/hysteresis_summary.json`
- `results/sensitivity_loose/`
- `RESULTS_HYSTERESIS.md`
- `selection.csv`
- `run.json`
- `figures/`

Run the discovery-only hysteresis stage with `uv run usgs-nwis hysteresis`.

`results/sensitivity_loose/` keeps the first-pass loop metrics without the
strict loop-quality filters, as a sensitivity check. The primary analysis is the
strict one.
