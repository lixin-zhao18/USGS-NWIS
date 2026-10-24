"""Interactive NASA Earthdata login and NLDAS access test.

Run this in your own terminal (not by pasting your password into chat):

    C:\\Users\\zhaol\\USGS-NWIS\\.venv\\Scripts\\python.exe ^
        C:\\Users\\zhaol\\USGS-NWIS\\scripts\\earthdata_login.py

The password prompt is hidden. Credentials are written to
C:\\Users\\zhaol\\.netrc by earthaccess.
"""

from __future__ import annotations

from pathlib import Path

import earthaccess

OUT = Path(".cache") / "earthdata_test"


def main() -> None:
    try:
        auth = earthaccess.login(strategy="netrc")
        print("Using existing ~/.netrc:", bool(auth))
    except Exception:
        print("No usable ~/.netrc; starting interactive login.")
        auth = earthaccess.login(strategy="interactive", persist=True)
        print("Login stored:", bool(auth))

    print("Searching one NLDAS_FORA0125_H granule for 2015-01-01 00:00 UTC...")
    results = earthaccess.search_data(
        short_name="NLDAS_FORA0125_H",
        temporal=("2015-01-01T00:00:00", "2015-01-01T00:59:59"),
        count=1,
    )
    if not results:
        raise RuntimeError("No NLDAS granule returned; check the Earthdata app approval.")
    print("Found one granule:", str(results[0])[:160])

    OUT.mkdir(parents=True, exist_ok=True)
    files = earthaccess.download(results, str(OUT))
    for path in files:
        size = Path(path).stat().st_size
        print(f"Downloaded {path} ({size:,} bytes)")
        if size < 100_000:
            raise RuntimeError("Downloaded file is too small; likely an HTML login page.")
    print("Earthdata login and NLDAS access OK.")


if __name__ == "__main__":
    main()
