# Forcing data notes for modeling

Practical notes for anyone (or any agent) who builds model inputs from the
NLDAS forcing in this repository. Every number below was checked against the
files; sources are named at the end.

## 1. What we have

| Product | Short name | Role | Variables | Period |
|---|---|---|---|---|
| NLDAS-2 Primary Forcing | `NLDAS_FORA0125_H` v2.0 | meteorology | 11 | 2015–2024 |
| NLDAS-2 Noah LSM | `NLDAS_NOAH0125_H` v2.0 | land-surface state | 5 | 2015–2024 |

Both are 0.125° hourly on the same grid and the same hourly time axis, so the
nearest-cell index computed for FORA works unchanged for Noah.

29 A-cohort stations, 2,542,488 rows, `2015-01-01T00:00Z`–`2024-12-31T23:00Z`,
87,672 hourly rows per station.

## 2. Files and schema

FORA and Noah are stored separately and joined on `(site_no, time)`.

`merged_2015_2024/all_stations_2015_2024.parquet` — FORA only, 2,542,488 × 13
(two keys plus the 11 variables below).

| Column | Unit | Meaning |
|---|---|---|
| `time` | UTC, hour-ending | see §3 |
| `site_no` | string | USGS station id, **keep as string** (leading zeros) |
| `Tair` | K | 2-m air temperature |
| `Qair` | kg kg⁻¹ | 2-m specific humidity |
| `PSurf` | Pa | surface pressure |
| `Wind_E`, `Wind_N` | m s⁻¹ | 10-m wind components |
| `LWdown`, `SWdown` | W m⁻² | downward long/short-wave radiation |
| `Rainf` | **mm per hour** | total precipitation (hourly total, see §4) |
| `CRainf_frac` | 0–1 | convective fraction of precipitation |
| `CAPE` | J kg⁻¹ | convective available potential energy |
| `PotEvap` | **mm per hour** | potential evaporation (hourly total, §4) |

`nldas_noah/<year>/all_stations.parquet` (or a merged ten-year equivalent) —
Noah only, seven columns: `time`, `site_no`, and the five state variables.

| Column | Unit | Meaning |
|---|---|---|
| `SoilM_0_10cm` | kg m⁻² = mm water | 0–10 cm soil moisture |
| `SoilM_10_40cm` | kg m⁻² = mm water | 10–40 cm |
| `SoilM_40_100cm` | kg m⁻² = mm water | 40–100 cm |
| `SoilM_100_200cm` | kg m⁻² = mm water | 100–200 cm |
| `SWE` | kg m⁻² = mm water | snow water equivalent |

`by_station/<site_no>.parquet` holds the same FORA columns for one station.
`annual_summary_2015_2024.csv` has annual rainfall, mean air temperature and
potential evaporation per station-year.

## 3. Time axis: hour-ending, UTC

The file stamped `A20150101.0000` covers **2014-12-31 23:00 → 2015-01-01
00:00**; `time_bnds` in the netCDF states this explicitly. FORA fluxes are
accumulated over the preceding hour, Noah states are instantaneous at the end
of that hour. Both products use the same convention, so **merge on `time`
directly — do not shift either product by one hour**.

`time` is timezone-aware UTC. Never localize to a US timezone before joining;
daylight saving creates duplicate and missing hours.

## 4. Unit traps

**`Rainf` and `PotEvap` are hourly totals, not rates.** The netCDF attribute is
`units = "kg m-2"` with no `s-1`. The numbers are already mm/h. Multiplying by
3600 (the GRIB convention for kg m⁻² s⁻¹) gives about 0.3 mm/year, which is
impossible.

**`PotEvap` must be clipped at zero before use as PET.** 418,334 rows (16.45%)
are negative, median −0.0098 mm/h, minimum −0.557 mm/h; 92.3% coincide with
`SWdown = 0` because net radiation is negative at night. This is a normal
model output, not corruption, but a model that expects non-negative PET will
misinterpret it as condensation.

```python
df["PotEvap"] = df["PotEvap"].clip(lower=0.0)
```

`PotEvap` is *potential* evaporation, not actual evapotranspiration. Do not
report it as ET.

**Soil moisture is water equivalent, not volumetric.** `SoilM_*` is kg m⁻²,
numerically mm of water in that layer. Convert to volumetric water content by
dividing by the layer thickness in mm:

| Layer | Thickness | Conversion |
|---|---|---|
| 0–10 cm | 100 mm | θ = `SoilM_0_10cm` / 100 |
| 10–40 cm | 300 mm | θ = `SoilM_10_40cm` / 300 |
| 40–100 cm | 600 mm | θ = `SoilM_40_100cm` / 600 |
| 100–200 cm | 1000 mm | θ = `SoilM_100_200cm` / 1000 |

## 5. Known data issues

- **FORA has no soil moisture.** Use `NLDAS_NOAH0125_H` (§1) or an antecedent
  precipitation index as a fallback.
- **Coastal land mask.** Station `04199500` (41.382 N, 82.317 W) sits beside
  Lake Erie; its nearest 0.125° cell is open water and every Noah soil-moisture
  value there is NaN. `scripts/nldas_noah_extract.py` detects this on the first
  file and moves the station to the nearest cell with data ((131,341) →
  (130,341)). FORA is unaffected because atmospheric variables exist over water.
- **No gaps.** 0 NaN, 0 duplicate `(site_no, time)`, 0 non-hourly steps across
  all 29 stations and 10 years.
- **Station ids are strings.** Reading `site_no` as an integer drops leading
  zeros and breaks joins.

## 6. Validation performed

| Check | Result |
|---|---|
| Merged parquet vs each yearly source | identical, max abs diff 0 |
| Row count per station | 87,672 (= 10 years hourly, leap days included) |
| Annual summary vs recomputation | max 0.05 mm (rounding) |
| Extractor vs raw Noah grid values | exact match, including the land-mask fallback |
| AORC 1-km 2015 cross-check (29 stations) | annual precipitation r = 0.944; short-wave ratio 1.00; wet-hour precipitation r = 0.705; NLDAS `Tair` warmer by ≈1.1 °C |

The AORC comparison covers 2015 only and 4 core variables (APCP, TMP, SPFH,
DSWRF); it is a sanity check, not a full reanalysis.

## 7. Storage and sharing

Not in Git and never should be: raw NLDAS netCDF (~17 GB per year), raw USGS
15-minute series (~300 MB), and the extracted forcing tables.

- Archive (single source of truth): desktop `D:\Stage II\nldas\`.
- Working copy: laptop, only what the current analysis needs.
- Code and processed summary tables: this repository.

## 8. Reproduction

```bash
# FORA, one year at a time (the extractor downloads the whole range to tmp)
python scripts/nldas_fora_extract.py --start 2015-01-01 --end 2015-12-31

# Noah soil moisture and SWE
python scripts/nldas_noah_extract.py --start 2015-01-01 --end 2015-12-31
```

Run one year per invocation. A ten-year request downloads roughly 120 GB before
extracting anything.

## Sources

- NLDAS-2 README, NASA GES DISC: `NLDAS2_README.pdf` (variable names, units,
  `time_bnds`).
- Verification report: `merged_2015_2024/VERIFICATION.md`, produced by
  `verify_merged.py` on 2026-10-26.
- AORC cross-check: `data/raw/forcing/aorc_validation/`, 2026-10-26.
