from __future__ import annotations

from usgs_nwis.rdb import parse_rdb


def test_parse_single_block() -> None:
    text = "\n".join(
        [
            "# comment",
            "agency_cd\tsite_no\tparm_cd",
            "5s\t15s\t5s",
            "USGS\t01010000\t00060",
            "USGS\t01010001\t00095",
        ]
    )
    frame = parse_rdb(text)
    assert list(frame.columns) == ["agency_cd", "site_no", "parm_cd"]
    assert len(frame) == 2
    assert frame["site_no"].tolist() == ["01010000", "01010001"]


def test_parse_repeated_headers_with_different_columns() -> None:
    text = "\n".join(
        [
            "agency_cd\tsite_no\tdatetime\t39671_00060",
            "5s\t15s\t20d\t14n",
            "USGS\t02336410\t2019-01-01 00:00\t80.6",
            "agency_cd\tsite_no\tdatetime\t39671_00060\t32368_00095",
            "5s\t15s\t20d\t14n\t14n",
            "USGS\t02310700\t2019-01-01 00:00\t-923\t4080",
        ]
    )
    frame = parse_rdb(text)
    assert len(frame) == 2
    assert "32368_00095" in frame.columns
    assert frame.loc[frame["site_no"] == "02310700", "32368_00095"].iloc[0] == "4080"
