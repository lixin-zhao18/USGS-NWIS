# Discovery-set SC–Q hysteresis diagnostics

- Generated: 2026-10-24T08:09:34+00:00
- Scope: 30 discovery stations only; the 10 validation stations are sealed.
- Method: Q-only event windows and normalised Q and SC; HI = SC_rise -
  SC_fall at the median overlapping Q; HI > 0 and positive signed area
  mean clockwise (rising limb above the falling limb at equal Q).

## Verdict

**MARGINAL PASS the frozen data-sufficiency gate** (18/30 = 60% of stations reproducible; gate 60%).

The gate is met exactly at its threshold: this supports a pilot model, not a definitive claim that the discovery set is representative.

- 3,747 usable discovery events; 893 pass the strict loop-quality filters.
- Direction is strongly asymmetric: 717 clockwise vs 154 counterclockwise.
- 24/30 stations are clockwise-dominant; median within-station direction fraction 0.82.

## By stratum

| stratum | stations | reproducible | median_loops | median_direction_fraction | median_loop_area |
|---|---|---|---|---|---|
| humid_rainfall | 17 | 12 | 29.0 | 0.833 | 0.313 |
| semiarid_flashy | 2 | 0 | 20.5 | 0.586 | 0.289 |
| snow_mixed | 11 | 6 | 22.0 | 0.824 | 0.305 |

## Failure modes (strict filter)

| reason | stations |
|---|---|
| too_few_loop_events | 6 |
| too_few_loop_events;direction_inconsistent;loop_area_too_small;split_half_direction_unstable | 2 |
| too_few_loop_events;direction_inconsistent;split_half_direction_unstable | 1 |
| split_half_direction_unstable | 1 |
| direction_inconsistent | 1 |
| too_few_loop_events;split_half_direction_unstable | 1 |

## Interpretation

- There is a reproducible clockwise SC–Q loop in humid and snow-influenced stations: SC is lower on the recession than on the rising limb at equal Q, consistent with dilution followed by slow chemical recovery.
- The semiarid stratum is not usable: 0/2 discovery stations pass. No semiarid claim should be made from this round.
- The strict filter retains only a quarter of usable events. Many events are multi-peak, SC does not recover, or the SC minimum is misplaced; those are excluded from the primary analysis.
- This is a feasibility result, not evidence that SC observes a unique connectivity state. Q-only predictability and the AI comparison are still open.

## Next decision

Run the Q-only baseline (predict SC or normalised SC within events from Q, dQ/dt and recent Q history with station-wise cross-validation). If it already explains >85% of SC variance, the hysteresis signal is not strong enough to justify the AI model.
