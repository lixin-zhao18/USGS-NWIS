# Figure captions and source data

## Figure 9 | Event complexity classes across the Tier-1 census

**a**, Counts of usable events assigned to simple closed loops (A), complex
loops (B), weak/open trajectories (C) and events without usable specific
conductance (X). **b**, Same classes by climate stratum. **c**, Event size
(peak minus pre-event base discharge, cfs) by class; boxes show the median and
interquartile range, whiskers extend to 1.5× the interquartile range, and points
are individual events. **d**, Number of simple loops versus weak/open events per
station; green circles mark stations that meet the reproducibility criteria and
the dashed line marks the 20-simple-loop data-sufficiency threshold.

Classes are assigned from Q/SC geometry and data completeness alone, before any
loop area or direction is computed. Class definitions: A, single Q peak,
monotonic rising and falling limbs, one SC excursion, SC recovery ≥50%; B,
multi-peak hydrograph, non-monotonic limbs, or multi-excursion SC; C, SC does
not recover, the SC minimum is misplaced, or the SC amplitude is too small; X,
insufficient paired coverage or too few native points.

Source data: `source_data/fig9_panel_a.csv`, `fig9_panel_b.csv`,
`fig9_panel_c.csv`, `fig9_panel_d.csv`. The underlying event-level table is
`../results/census_event_metrics.csv`.

## Note on event-size confounding

Simple loops are systematically larger events than complex or weak events
(median event size 214 vs 54 vs 55 cfs). Comparisons between classes must
therefore control for event size, for example by matching events within size
deciles.
