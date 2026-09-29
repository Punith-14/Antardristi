"""
Evidence record: the layer between analysis and language.

Every quantitative claim the system makes must exist here first, with the method
that produced it and the spatial support it was computed over. The report
generator is given this and nothing else, which is what makes the generated text
checkable.

See contracts/analysis_response.json for the agreed shape.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Evidence:
    """One measured or derived quantity."""

    id: str
    quantity: str
    value: float
    unit: str
    method: str | None = None
    model: str | None = None
    threshold: float | None = None
    threshold_unit: str | None = None
    confidence: float | None = None
    spatial_support_km2: float | None = None
    source: str | None = None
    denominator: str | None = None
    derived_from: list[str] = field(default_factory=list)
    period_ref: str | None = None
    note: str | None = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


@dataclass
class Observation:
    """How much of the region the sensor actually saw."""

    sensor_used: str
    observable_area_km2: float
    region_area_km2: float
    scenes_available: int = 0
    scenes_used: int = 0
    cloud_fraction: float | None = None
    sensor_reason: str | None = None
    sensors_considered: list[str] = field(default_factory=list)

    @property
    def coverage_fraction(self):
        if not self.region_area_km2:
            return 0.0
        return self.observable_area_km2 / self.region_area_km2

    @property
    def scenes_rejected_by_cloud_filter(self):
        return max(self.scenes_available - self.scenes_used, 0)

    def to_dict(self):
        payload = asdict(self)
        payload.pop("region_area_km2", None)
        payload["coverage_fraction"] = round(self.coverage_fraction, 4)
        payload["scenes_rejected_by_cloud_filter"] = self.scenes_rejected_by_cloud_filter
        return {k: v for k, v in payload.items() if v not in (None, [], "")}


class EvidenceBuilder:
    """Collects evidence, assigns sequential ids, enforces the basics."""

    def __init__(self):
        self._items: list[Evidence] = []
        self._notes: list[str] = []

    def add(self, quantity, value, unit, **kwargs):
        if value is None:
            raise ValueError(
                f"Refusing to record evidence '{quantity}' with a null value. "
                "If the quantity could not be measured, record it as unobserved "
                "instead - a missing measurement is not the same as zero."
            )

        derived_from = kwargs.get("derived_from") or []
        known = {item.id for item in self._items}
        for ref in derived_from:
            if ref not in known:
                raise ValueError(
                    f"Evidence '{quantity}' derives from unknown id '{ref}'."
                )

        item = Evidence(
            id=f"E{len(self._items) + 1}",
            quantity=quantity,
            value=value,
            unit=unit,
            **kwargs,
        )
        self._items.append(item)
        return item.id

    def note(self, text):
        """Something the system could not observe or wants to caveat."""
        self._notes.append(text)

    @property
    def notes(self):
        return list(self._notes)

    def values_by_id(self):
        return {item.id: item for item in self._items}

    def to_list(self):
        return [item.to_dict() for item in self._items]

    def __len__(self):
        return len(self._items)


def coverage_warning(observation, threshold=0.9):
    """Return a caveat string when the result rests on partial observation.

    Our own Kerala August 2019 test collapsed from 37,575 km2 observable to
    6,820 km2 - and reported LESS water during a major flood, because the only
    visible pixels were the cloud-free, therefore un-rained-on, therefore
    un-flooded ones. A headline figure without its coverage is misleading.
    """
    fraction = observation.coverage_fraction
    if fraction >= threshold:
        return None
    return (
        f"Only {fraction * 100:.1f}% of the region could be observed. "
        "Figures are computed over that portion and may not represent the "
        "whole area."
    )


def build_provenance(pipeline_version, datasets, models=None, stats_scale_m=None,
                     known_confusions=None):
    return {
        "pipeline_version": pipeline_version,
        "models": models or [],
        "datasets": datasets,
        "stats_scale_m": stats_scale_m,
        "known_confusions": known_confusions or [],
    }
