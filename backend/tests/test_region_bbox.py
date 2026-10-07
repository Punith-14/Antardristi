"""Results carry their region's bounding box so the map can frame them
(found live: vegetation health in Ludhiana left the map on all of India)."""

import pytest

pytest.importorskip("ee")

from pipeline import analysis  # noqa: E402


class Info:
    def __init__(self, value):
        self.value = value

    def get(self, i):
        return Info(self.value[i])

    def getInfo(self):
        return self.value


class FakeGeometry:
    calls = 0

    def bounds(self, maxError=None):
        FakeGeometry.calls += 1
        return self

    def coordinates(self):
        return Info([[[75.4, 30.6], [76.4, 30.6], [76.4, 31.1], [75.4, 31.1], [75.4, 30.6]]])


class Broken:
    def bounds(self, maxError=None):
        raise RuntimeError("Earth Engine busy")


def test_bbox_is_west_south_east_north_and_asked_once():
    analysis._BBOX.clear()
    key = ("2015", "level2", "Ludhiana", "Punjab")
    assert analysis.region_bbox(FakeGeometry(), key) == [75.4, 30.6, 76.4, 31.1]
    assert analysis.region_bbox(FakeGeometry(), key) == [75.4, 30.6, 76.4, 31.1]
    assert FakeGeometry.calls == 1


def test_a_failure_only_costs_the_framing():
    analysis._BBOX.clear()
    assert analysis.region_bbox(Broken(), ("x",)) is None
    assert ("x",) not in analysis._BBOX, "a failure is not remembered"
