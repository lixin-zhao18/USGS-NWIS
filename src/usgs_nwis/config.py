"""Typed access to a round configuration file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PARAM_NAMES = {"streamflow": "00060", "specific_conductance": "00095"}


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    years: tuple[int, ...]

    @property
    def start_utc(self) -> str:
        return self.start.strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def end_utc(self) -> str:
        return self.end.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class DataSources:
    site_service: str
    iv_service: str
    gages2_item: str
    gages2_file: str
    states: tuple[str, ...]
    batch_sites: int
    concurrency: int
    request_timeout_s: int
    retries: int


@dataclass(frozen=True)
class CandidateRules:
    area_min_km2: float
    area_max_km2: float
    preferred_area_min_km2: float
    preferred_area_max_km2: float
    max_dev_pct: float
    max_dam_density: float
    exclude_name_patterns: tuple[str, ...]


@dataclass(frozen=True)
class QualityRules:
    max_cadence_minutes: float
    min_qualifying_years: int
    min_paired_completeness: float
    approved_only_primary: bool
    pairing_tolerance_fraction: float
    min_sc_median_us_cm: float
    max_sc_median_us_cm: float
    max_negative_fraction: float


@dataclass(frozen=True)
class EventRules:
    min_count_per_station: int
    rolling_baseline_hours: int
    min_peak_distance_hours: int
    min_event_hours: float
    max_event_hours: float
    min_limb_points: int
    min_event_sc_completeness: float
    min_event_q_completeness: float
    peak_prominence_log10: float
    recession_fraction: float


@dataclass(frozen=True)
class StrataRules:
    snow_pct_min: float
    aridity_max: float
    order: tuple[str, ...]


@dataclass(frozen=True)
class SelectionRules:
    target_total: int
    strata: dict[str, int]
    discovery: dict[str, int]
    validation: dict[str, int]
    reallocation_order: tuple[str, ...]
    rank_weights: dict[str, float]
    max_per_huc8: int


@dataclass(frozen=True)
class Config:
    path: Path
    raw: dict[str, Any]
    experiment_id: str
    seed: int
    window: Window
    params: dict[str, str]
    data: DataSources
    candidate: CandidateRules
    quality: QualityRules
    events: EventRules
    strata: StrataRules
    selection: SelectionRules

    @property
    def root(self) -> Path:
        return self.path.resolve().parent.parent

    @property
    def experiment_dir(self) -> Path:
        return self.root / "experiments" / self.experiment_id

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def results_dir(self) -> Path:
        return self.experiment_dir / "results"

    @property
    def figures_dir(self) -> Path:
        return self.experiment_dir / "figures"

    def param_roles(self) -> dict[str, str]:
        return {v: k for k, v in self.params.items()}


def _dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    window_raw = raw["window"]
    start = _dt(str(window_raw["start"]))
    end = _dt(str(window_raw["end"]))
    years = tuple(range(start.year, end.year + 1))

    data_raw = raw["data_sources"]
    cand_raw = raw["candidate"]
    qual_raw = raw["quality"]
    ev_raw = raw["events"]
    strata_raw = raw["strata"]
    sel_raw = raw["selection"]

    return Config(
        path=path,
        raw=raw,
        experiment_id=str(raw["experiment_id"]),
        seed=int(raw["seed"]),
        window=Window(start=start, end=end, years=years),
        params={k: str(v) for k, v in raw["parameters"].items()},
        data=DataSources(
            site_service=str(data_raw["site_service"]),
            iv_service=str(data_raw["iv_service"]),
            gages2_item=str(data_raw["gages2_item"]),
            gages2_file=str(data_raw["gages2_file"]),
            states=tuple(str(s) for s in data_raw["states"]),
            batch_sites=int(data_raw["batch_sites"]),
            concurrency=int(data_raw["concurrency"]),
            request_timeout_s=int(data_raw["request_timeout_s"]),
            retries=int(data_raw["retries"]),
        ),
        candidate=CandidateRules(
            area_min_km2=float(cand_raw["area_km2"][0]),
            area_max_km2=float(cand_raw["area_km2"][1]),
            preferred_area_min_km2=float(cand_raw["preferred_area_km2"][0]),
            preferred_area_max_km2=float(cand_raw["preferred_area_km2"][1]),
            max_dev_pct=float(cand_raw["max_dev_pct"]),
            max_dam_density=float(cand_raw["max_dam_density"]),
            exclude_name_patterns=tuple(
                str(p) for p in cand_raw["exclude_name_patterns"]
            ),
        ),
        quality=QualityRules(
            max_cadence_minutes=float(qual_raw["max_cadence_minutes"]),
            min_qualifying_years=int(qual_raw["min_qualifying_years"]),
            min_paired_completeness=float(qual_raw["min_paired_completeness"]),
            approved_only_primary=bool(qual_raw["approved_only_primary"]),
            pairing_tolerance_fraction=float(qual_raw["pairing_tolerance_fraction"]),
            min_sc_median_us_cm=float(qual_raw["min_sc_median_us_cm"]),
            max_sc_median_us_cm=float(qual_raw["max_sc_median_us_cm"]),
            max_negative_fraction=float(qual_raw["max_negative_fraction"]),
        ),
        events=EventRules(
            min_count_per_station=int(ev_raw["min_count_per_station"]),
            rolling_baseline_hours=int(ev_raw["rolling_baseline_hours"]),
            min_peak_distance_hours=int(ev_raw["min_peak_distance_hours"]),
            min_event_hours=float(ev_raw["min_event_hours"]),
            max_event_hours=float(ev_raw["max_event_hours"]),
            min_limb_points=int(ev_raw["min_limb_points"]),
            min_event_sc_completeness=float(ev_raw["min_event_sc_completeness"]),
            min_event_q_completeness=float(ev_raw["min_event_q_completeness"]),
            peak_prominence_log10=float(ev_raw["peak_prominence_log10"]),
            recession_fraction=float(ev_raw["recession_fraction"]),
        ),
        strata=StrataRules(
            snow_pct_min=float(strata_raw["snow_pct_min"]),
            aridity_max=float(strata_raw["aridity_max"]),
            order=tuple(str(s) for s in strata_raw["order"]),
        ),
        selection=SelectionRules(
            target_total=int(sel_raw["target_total"]),
            strata={k: int(v) for k, v in sel_raw["strata"].items()},
            discovery={k: int(v) for k, v in sel_raw["discovery"].items()},
            validation={k: int(v) for k, v in sel_raw["validation"].items()},
            reallocation_order=tuple(str(s) for s in sel_raw["reallocation_order"]),
            rank_weights={k: float(v) for k, v in sel_raw["rank_weights"].items()},
            max_per_huc8=int(sel_raw.get("max_per_huc8", 1)),
        ),
    )
