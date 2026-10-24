"""Parser for USGS RDB (tab-delimited) responses.

The legacy site service emits one header block per response, while the
multi-site instantaneous-values service repeats the header whenever the set of
time-series columns changes.  Both shapes are handled here.
"""

from __future__ import annotations

import re

import pandas as pd

_FORMAT_TOKEN = re.compile(r"^\d+[snd]$")


def _is_format_line(tokens: list[str]) -> bool:
    return bool(tokens) and all(_FORMAT_TOKEN.match(token) for token in tokens)


def parse_rdb(text: str) -> pd.DataFrame:
    blocks: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] | None = None
    rows: list[list[str]] = []
    skip_next_format = False

    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        tokens = line.split("\t")
        if line.startswith("agency_cd\t") or tokens[0] == "agency_cd":
            if header is not None:
                blocks.append((header, rows))
            header = tokens
            rows = []
            skip_next_format = True
            continue
        if skip_next_format and _is_format_line(tokens):
            skip_next_format = False
            continue
        skip_next_format = False
        if header is not None and len(tokens) == len(header):
            rows.append(tokens)

    if header is not None:
        blocks.append((header, rows))

    frames = [pd.DataFrame(data, columns=cols) for cols, data in blocks if data]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)
