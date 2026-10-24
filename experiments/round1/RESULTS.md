# Round 1 results — catchment screening

- Generated: 2026-10-24T01:54:21+00:00
- Seed: `20261023`
- Window: 2015-01-01 to 2024-12-31 (UTC); Q=`00060`, SC=`00095`
- Bulk data: legacy multi-site instantaneous-values service (`nwis.waterservices.usgs.gov`); attributes: GAGES-II (Falcone 2011).

## Screening funnel

| step | n_sites |
|---|---|
| catalog_sites_both_params | 1265 |
| catalog_overlap_ge_3y | 824 |
| gages2_matched | 495 |
| area_within_range | 251 |
| name_flags_clear | 242 |
| disturbance_limits | 114 |
| candidate_ok | 114 |
| downloaded | 114 |
| qc_pass | 80 |
| usable_events_ge_min | 50 |
| selected | 40 |

## Strata: target and achieved

| Stratum | Target | Selected | Target − selected |
|---|---:|---:|---:|
| semiarid_flashy | 10 | 3 | 7 |
| snow_mixed | 10 | 14 | -4 |
| humid_rainfall | 20 | 23 | -3 |

Negative differences are slots reallocated to strata that still had eligible independent catchments.

Selected **40** stations: 30 discovery / 10 validation.

## Selected catchments

| site_no | station_name | state | area_km2 | gages_class | dev_pct | aridity_index | snow_pct | stratum | split | n_years_qualifying | median_paired_completeness | n_usable_events |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 03302050 | BRIER CREEK AT PENDELTON ROAD NEAR LOUISVILLE, KY | KY | 10.7 | Ref | 0.8 | 1.49 | 10.9 | humid_rainfall | validation | 8 | 0.955 | 111 |
| 07061270 | East Fork Black River near Lesterville, MO | MO | 135.4 | Ref | 1.7 | 1.47 | 9.6 | humid_rainfall | discovery | 10 | 0.924 | 68 |
| 06889160 | SOLDIER C NR CIRCLEVILLE, KS | KS | 129.0 | Ref | 4.2 | 1.15 | 8.0 | humid_rainfall | discovery | 3 | 0.858 | 79 |
| 01632900 | SMITH CREEK NEAR NEW MARKET, VA | VA | 245.0 | Ref | 8.8 | 1.41 | 13.6 | humid_rainfall | discovery | 10 | 0.946 | 61 |
| 03297800 | CEDAR CREEK AT HWY 1442 NEAR SHEPHERDSVILLE, KY | KY | 31.4 | Non-ref | 5.3 | 1.52 | 10.0 | humid_rainfall | discovery | 7 | 0.908 | 223 |
| 04188496 | Eagle Creek above Findlay OH | OH | 134.7 | Non-ref | 9.0 | 1.35 | 19.0 | humid_rainfall | discovery | 7 | 0.91 | 270 |
| 07263296 | Maumelle River near Wye, AR | AR | 185.5 | Non-ref | 4.2 | 1.49 | 3.0 | humid_rainfall | discovery | 10 | 0.987 | 71 |
| 03512000 | OCONALUFTEE RIVER AT BIRDTOWN, NC | NC | 476.0 | Non-ref | 3.8 | 2.71 | 13.6 | humid_rainfall | validation | 10 | 0.971 | 182 |
| 08052745 | Doe Br at US Hwy 380 nr Prosper, TX | TX | 100.0 | Non-ref | 12.9 | 1.03 | 1.0 | humid_rainfall | discovery | 7 | 0.959 | 394 |
| 02298202 | SHELL CREEK NEAR PUNTA GORDA FL | FL | 886.2 | Non-ref | 2.6 | 1.11 | 0.0 | humid_rainfall | discovery | 10 | 0.996 | 104 |
| 03292470 | HARRODS CREEK AT HIGHWAY 329 NR GOSHEN, KY. | KY | 181.9 | Non-ref | 9.0 | 1.59 | 12.8 | humid_rainfall | validation | 8 | 0.902 | 133 |
| 01478120 | East Branch White Clay Creek at Avondale, PA | PA | 31.6 | Non-ref | 14.2 | 1.69 | 16.5 | humid_rainfall | discovery | 5 | 0.98 | 149 |
| 01465850 | South Branch Rancocas Creek at Vincentown NJ | NJ | 170.8 | Non-ref | 10.7 | 1.61 | 14.7 | humid_rainfall | validation | 4 | 0.994 | 61 |
| 03072000 | Dunkard Creek at Shannopin, PA | PA | 588.4 | Non-ref | 6.5 | 1.65 | 17.0 | humid_rainfall | discovery | 9 | 0.955 | 113 |
| 11462500 | RUSSIAN R NR HOPLAND CA | CA | 938.7 | Non-ref | 7.6 | 1.53 | 2.0 | humid_rainfall | validation | 10 | 0.978 | 66 |
| 07048550 | West Fork White River east of Fayetteville, AR | AR | 317.6 | Non-ref | 12.7 | 1.58 | 5.8 | humid_rainfall | discovery | 9 | 0.973 | 243 |
| 03353200 | EAGLE CREEK AT ZIONSVILLE, IN | IN | 273.9 | Non-ref | 12.5 | 1.43 | 17.0 | humid_rainfall | discovery | 10 | 0.947 | 148 |
| 04199500 | Vermilion River near Vermilion OH | OH | 672.4 | Non-ref | 6.9 | 1.47 | 19.9 | humid_rainfall | discovery | 4 | 0.857 | 87 |
| 02389150 | ETOWAH RIVER AT GA 9, NEAR DAWSONVILLE, GA | GA | 340.4 | Non-ref | 5.8 | 2.15 | 5.6 | humid_rainfall | validation | 6 | 0.903 | 92 |
| 03073000 | South Fork Tenmile Creek at Jefferson, PA | PA | 467.2 | Non-ref | 9.0 | 1.58 | 17.5 | humid_rainfall | discovery | 9 | 0.914 | 152 |
| 03275500 | EAST FORK WHITEWATER RIVER AT RICHMOND, IN | IN | 313.3 | Non-ref | 13.8 | 1.46 | 18.5 | humid_rainfall | discovery | 7 | 0.913 | 83 |
| 03106000 | Connoquenessing Creek near Zelienople, PA | PA | 921.4 | Non-ref | 18.9 | 1.61 | 19.3 | humid_rainfall | discovery | 9 | 0.951 | 157 |
| 01464500 | Crosswicks Creek at Extonville NJ | NJ | 200.8 | Non-ref | 14.7 | 1.66 | 15.1 | humid_rainfall | discovery | 5 | 0.993 | 78 |
| 08086290 | Big Sandy Ck abv Breckenridge, TX | TX | 733.0 | Ref | 6.4 | 0.7 | 0.9 | semiarid_flashy | discovery | 3 | 0.856 | 142 |
| 09371492 | MUD CREEK AT STATE HIGHWAY 32, NEAR CORTEZ, CO. | CO | 87.3 | Non-ref | 3.8 | 0.6 | 25.1 | semiarid_flashy | discovery | 6 | 0.828 | 87 |
| 09371520 | MCELMO CREEK ABOVE TRAIL CANYON NEAR CORTEZ, CO | CO | 607.0 | Non-ref | 4.7 | 0.65 | 26.7 | semiarid_flashy | validation | 4 | 0.822 | 101 |
| 14138900 | NORTH FORK BULL RUN RIVER NEAR MULTNOMAH FALLS, OR | OR | 21.7 | Ref | 0.0 | 5.89 | 32.0 | snow_mixed | validation | 10 | 0.996 | 67 |
| 04043244 | EAST BRANCH SALMON TROUT RIVER NEAR DODGE CITY, MI | MI | 26.6 | Ref | 2.4 | 1.56 | 31.5 | snow_mixed | discovery | 7 | 0.945 | 157 |
| 01434017 | EAST BRANCH NEVERSINK RIVER NEAR CLARYVILLE NY | NY | 59.5 | Ref | 0.5 | 3.07 | 33.2 | snow_mixed | discovery | 3 | 0.878 | 62 |
| 01451800 | Jordan Creek near Schnecksville, PA | PA | 135.8 | Ref | 10.6 | 1.82 | 21.0 | snow_mixed | discovery | 5 | 0.958 | 80 |
| 11204100 | SF TULE R NR RESERVATION BNDRY NR PORTERVILLE CA | CA | 249.1 | Ref | 0.1 | 1.14 | 25.2 | snow_mixed | discovery | 6 | 0.925 | 71 |
| 01544500 | Kettle Creek at Cross Fork, PA | PA | 355.1 | Ref | 0.9 | 1.86 | 26.4 | snow_mixed | discovery | 9 | 0.948 | 57 |
| 03066000 | BLACKWATER RIVER AT DAVIS, WV | WV | 222.8 | Ref | 4.8 | 2.51 | 25.4 | snow_mixed | discovery | 10 | 0.934 | 88 |
| 03067510 | SHAVERS FORK NR CHEAT BRIDGE, WV | WV | 154.6 | Non-ref | 2.6 | 2.83 | 27.2 | snow_mixed | discovery | 10 | 0.91 | 130 |
| 01516350 | Tioga River near Mansfield, PA | PA | 395.2 | Non-ref | 4.8 | 1.67 | 25.8 | snow_mixed | discovery | 8 | 0.939 | 85 |
| 10133650 | E CANYON CR BL I-80 REST STOP NR PARK CITY, UT | UT | 89.6 | Non-ref | 9.7 | 1.5 | 52.6 | snow_mixed | discovery | 10 | 0.867 | 138 |
| 03079000 | Casselman River at Markleton, PA | PA | 990.8 | Non-ref | 7.2 | 1.93 | 24.6 | snow_mixed | validation | 9 | 0.981 | 108 |
| 03034000 | Mahoning Creek at Punxsutawney, PA | PA | 407.6 | Non-ref | 10.0 | 1.89 | 23.4 | snow_mixed | discovery | 8 | 0.928 | 114 |
| 01595800 | NORTH BRANCH POTOMAC RIVER AT BARNUM, WV | WV | 693.9 | Non-ref | 3.5 | 2.02 | 24.2 | snow_mixed | validation | 9 | 0.973 | 141 |
| 09041400 | MUDDY CRK BLW WOLFORD MTN RESER. NR KREMMLING, CO | CO | 700.9 | Non-ref | 0.8 | 1.36 | 48.8 | snow_mixed | discovery | 10 | 0.976 | 56 |

## Data-quality envelope

- Downloaded candidates: 114; QC pass (≥3 qualifying years): 80.
- Stations with ≥50 usable events: 50.
- Selected median paired completeness: 0.946; selected usable events: median 102, range 56–394.
- Selected area: median 247 km² (range 11–991); inside 10–200 km²: 17.
- GAGES-II reference: 12; low-disturbance flag: 20.

## Limitations

- The candidate pool is restricted to GAGES-II conterminous gages, so NWIS-only sites without consistent area/climate/disturbance attributes are excluded.
- True semiarid small catchments with continuous SC are effectively absent from the USGS network. The stratum is undersubscribed; slots are reallocated rather than filling it with humid sites relabelled as dry.
- Winter SC gaps are common in snow-dominated basins; usable-event counts therefore favour sites with year-round SC coverage.
- No SC–Q hysteresis metric has been computed in this round. The validation split remains unopened.

## Next step

Compute event-scale SC–Q hysteresis descriptors (direction, loop area, HI/FI, amplitude) on the discovery split only, with event-level reproducibility checks before any model work.
