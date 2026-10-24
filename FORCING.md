# Forcing data for the A-class pilot

## Scope

- 29 independent A-cohort stations, 1,248 simple closed-loop events.
- Event windows: 96 h before event start through 12 h after event end.
- Hourly, UTC.

## AORC v1.1 (1-km, hourly)

- Store: `s3://noaa-nws-aorc-v1-1-1km/{year}.zarr` (anonymous).
- Variables: `APCP_surface` (kg m⁻² = mm), `TMP_2maboveground` (K),
  `SPFH_2maboveground` (kg kg⁻¹), `PRES_surface` (Pa), `DLWRF_surface`
  (W m⁻²), `DSWRF_surface` (W m⁻²), `UGRD_10maboveground` and
  `VGRD_10maboveground` (m s⁻¹).
- Grid: 0.01° latitude/longitude, 4,201 × 8,401; the nearest cell is used.
- Access: anonymous S3 through xarray/zarr. No account.
- Chunking: 144 h × 128 × 256. Point extraction reads whole chunks, so a
  full 29-station × 10-year × 8-variable extraction is hundreds of GB of
  network reads and several hours. AORC is therefore used as a 1-km
  validation subset, not as the primary full-period forcing.

### Completed validation

`data/raw/forcing/aorc_validation/`: 29 stations × 2015 × 4 core variables
(APCP, TMP, SPFH, DSWRF), 8,760 hourly rows per station, 4.0 MB total.
Sanity check: Neversink annual precipitation 1,437 mm, mean temperature
6.6 °C.

```bash
uv run usgs-nwis forcing --source aorc --years 2015-2015 \
  --variables core --workers 3 --out-name aorc_validation
```

## NLDAS-2 (0.125°, hourly)

- Product: `NLDAS_FORA0125_H` v2.0, GES DISC, DOI 10.5067/THUF4J1RLSYG.
- Access: **free NASA Earthdata login required.** Anonymous requests return a
  302 redirect to `urs.earthdata.nasa.gov`; the legacy OPeNDAP endpoint is
  410 Gone.
- Direct file pattern:
  `https://hydro1.gesdisc.eosdis.nasa.gov/data/NLDAS/NLDAS_FORA0125_H.2.0/{year}/{doy}/NLDAS_FORA0125_H.A{yyyymmdd}.{hh}00.020.nc`
  (about 1.6 MB per hour; 87,600 files for 2015–2024).
- Preferred access: GES DISC Subsetter/Regridder or a point/time subset via
  Earthdata Search, so that one request returns the needed variables and
  period instead of 87,600 hourly files.
- Variables for the pilot: soil moisture (0–10, 10–40, 40–100, 100–200 cm),
  snow water equivalent, 2-m air temperature, precipitation, specific
  humidity, short/long-wave radiation, wind and surface pressure.

### Configure Earthdata

1. Register: https://urs.earthdata.nasa.gov/users/new
2. Approve the GES DISC application when prompted.
3. Create `%USERPROFILE%\.netrc`:

   ```
   machine urs.earthdata.nasa.gov
     login <username>
     password <password>
   ```

   Do not commit this file or paste credentials into chat.

## Recommended split

- **NLDAS-2**: primary hourly forcing for the 29-station × 10-year pilot.
- **AORC**: 3–5 station × 1–2 year 1-km validation of NLDAS precipitation and
  temperature, plus event-window checks.
- If Earthdata access is not available, run the full AORC extraction on the
  Linux server, not on the laptop; it is network-heavy.
