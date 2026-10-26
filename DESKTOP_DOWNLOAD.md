# Running the NLDAS/AORC download on the desktop

This is the step-by-step record for preparing hourly forcing for the 29
A-cohort stations on a second Windows machine.

## 1. What you need

- Git
- [uv](https://docs.astral.sh/uv/) (it installs Python and dependencies)
- A free NASA Earthdata account
- About **20 GB free disk** at any time (raw files are deleted after each year)
- Stable network; 2016–2024 takes several hours

Final output is only **~150–200 MB** of per-station Parquet.

## 2. Clone both repositories side by side

```powershell
cd C:\
mkdir work
cd work
git clone https://github.com/lixin-zhao18/USGS-NWIS.git
git clone https://github.com/lixin-zhao18/usgs-qsc-hysteresis-dataset.git
```

The NLDAS script reads the A-cohort station list from
`..\usgs-qsc-hysteresis-dataset\data\stations.csv` by default.

## 3. Earthdata login

Register at <https://urs.earthdata.nasa.gov/users/new> if needed. Then create
**both** files in your user profile (`%USERPROFILE%`), because some tools look
for `.netrc` and `earthaccess` on Windows looks for `_netrc`:

```
machine urs.earthdata.nasa.gov
  login YOUR_USERNAME
  password YOUR_PASSWORD
```

Save as:

- `C:\Users\<you>\.netrc`
- `C:\Users\<you>\_netrc`

Then authorize the GES DISC application and accept its EULA:

1. <https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ>
   → log in → **Authorize**.
2. Open one file in a browser and accept the EULA if prompted:
   `https://data.gesdisc.earthdata.nasa.gov/data/NLDAS/NLDAS_FORA0125_H.2.0/2015/001/NLDAS_FORA0125_H.A20150101.0000.020.nc`

Do not commit these files or paste the password into chat.

Test the login and download:

```powershell
cd C:\work\USGS-NWIS
uv sync --extra forcing
uv run python scripts/earthdata_login.py
```

Success ends with `Earthdata login and NLDAS access OK.`

## 4. Download NLDAS-2 FORA, one year at a time

```powershell
cd C:\work\USGS-NWIS
foreach ($y in 2016..2024) {
  uv run python scripts/nldas_fora_extract.py `
    --start "$y-01-01" --end "$y-12-31" --threads 8
}
```

Linux/macOS:

```bash
for y in $(seq 2016 2024); do
  uv run python scripts/nldas_fora_extract.py --start "$y-01-01" --end "$y-12-31" --threads 8
done
```

What happens for each year:

- searches and downloads 8,760 hourly NLDAS files (~17 GB),
- extracts the nearest 0.125° grid cell for all 29 stations,
- deletes the raw files,
- writes `data/raw/forcing/nldas_fora/<start>_<end>/`.

If a year already has `all_stations.parquet`, it is skipped. Use
`--overwrite` to force a rerun.

Test one day first if you want:

```powershell
uv run python scripts/nldas_fora_extract.py --start 2015-01-01 --end 2015-01-01
```

## 5. Variables and units

`NLDAS_FORA0125_H` (v2.0) contains atmospheric forcing only:

| Variable | Unit | Note |
|---|---|---|
| `Tair` | K | 2-m air temperature |
| `Qair` | kg kg⁻¹ | 2-m specific humidity |
| `PSurf` | Pa | surface pressure |
| `Wind_E`, `Wind_N` | m s⁻¹ | 10-m wind components |
| `LWdown`, `SWdown` | W m⁻² | long/short-wave radiation |
| `Rainf` | kg m⁻² (hourly total) | numerically mm h⁻¹; **do not** multiply by 3600 |
| `CRainf_frac` | fraction | convective precipitation fraction |
| `CAPE` | J kg⁻¹ | convective available potential energy |
| `PotEvap` | kg m⁻² (hourly total) | potential evaporation; same handling as `Rainf` |

`Rainf` and `PotEvap` are stored as **hourly totals**, not rates. The netCDF
attribute is `units = "kg m-2"` with no `s-1`. Checked against the raw files on
2026-10-26: a 2015 station-day summed from `Rainf` gives 49 mm while the same
day under AORC gives 72 mm, and the desktop 2016–2024 run reports 788–2,183 mm.
Treating the values as kg m⁻² s⁻¹ would imply 0.3 mm/year, which is impossible.

**There is no soil moisture in FORA.** For the first model version use an
antecedent precipitation index (API) from `Rainf`. Soil moisture requires
`NLDAS_NOAH0125_H` or SMAP and is a separate, larger download.

## 6. AORC validation (optional, 1-km)

AORC is anonymous (no Earthdata):

```powershell
uv run usgs-nwis forcing --source aorc --years 2015-2015 `
  --variables core --workers 3 --out-name aorc_validation
```

Output: `data/raw/forcing/aorc_validation/`, 29 stations × 2015 × 4 variables,
~4 MB.

## 7. What goes to GitHub

| Content | GitHub? |
|---|---|
| Code, scripts, configs | Yes — `USGS-NWIS` |
| Processed event/station tables, class labels, figures | Yes — `usgs-qsc-hysteresis-dataset` |
| NLDAS/AORC extracted point Parquet (~150–200 MB) | **Release asset**, not a normal commit |
| Raw 15-min USGS series (~365 MB) | No — reproducible via `usgs-nwis download` |
| Raw NLDAS hourly files (~17 GB/year) | No — deleted after extraction |
| Earthdata credentials | Never |

A GitHub Release can hold a zip of the final forcing Parquet (assets up to
2 GB). That keeps the repository small while making the data downloadable.

## 8. Troubleshooting

- **`302` / `EulaNotAccepted`**: repeat the GES DISC Authorize link and open the
  `.nc` URL in a browser to accept the EULA.
- **`No .netrc found at ...\_netrc`**: create `_netrc` as well as `.netrc`.
- **Interactive login EOF**: do not run the login script through a
  non-interactive shell; create the `.netrc`/`_netrc` files manually.
- **Slow download**: reduce `--threads` to 4; Earthdata rate-limits aggressive
  clients. The script skips unreadable files.
- **Disk**: each year peaks at ~17 GB and then drops to ~20 MB; do not run two
  years in parallel.
