"""
One synthetic flood, observable by both sensors, with every answer known.

A 20 x 20 km region (400 km2). Each pixel is 1 km2.

    permanent water   rows 15-17, cols 15-17          9 km2 (JRC mask)
    post-event water  rows  2-9,  cols  2-9          64 km2 of flood
                      + the permanent water           9 km2
    pre-event water   rows  2-3,  cols  2-9          16 km2
                      + the permanent water           9 km2

SAR sees everything except a strip at the western edge (cols 0-1, 40 km2)
outside the orbit's swath, like a real single-orbit footprint.

Optical sees everything except a cloud (rows 0-5, cols 12-19, 48 km2) in the
post window, and a different cloud (rows 10-13, cols 0-7, 32 km2) in the
pre window. The flood itself is clear in the post window, so both sensors
should report 64 km2 of flood - and the optical baseline has to be measured
only where both windows were clear, or the difference in cloud becomes a
difference in water.
"""

from detection import optical, sar
from geo import zones as zone_extraction
from pipeline import analysis

from fake_ee import FakeCollection, FakeImage, FakeRegion, Info, fake_area_km2, grid

SHAPE = (20, 20)
PERMANENT = grid(SHAPE, (15, 15, 18, 18))
POST_FLOOD = grid(SHAPE, (2, 2, 10, 10))
PRE_WATER = grid(SHAPE, (2, 2, 4, 10))

SAR_VALID = grid(SHAPE, (0, 0, 20, 2), value=False)          # swath edge missing
S2_POST_VALID = grid(SHAPE, (0, 12, 6, 20), value=False)     # post-window cloud
S2_PRE_VALID = grid(SHAPE, (10, 0, 14, 8), value=False)      # pre-window cloud

POST = ("2018-08-01", "2018-08-31")
PRE = ("2018-05-01", "2018-05-31")

META = {
    "slug": "testland", "name": "Testland", "admin_level": "state",
    "state": "Testland", "boundary_source": "FAO/GAUL/2015/level1",
    "boundary_vintage": "2015",
}


def _water(window):
    return (POST_FLOOD | PERMANENT) if window == POST[0] else (PRE_WATER | PERMANENT)


def fake_sar_detect(region, start_date, end_date, relative_orbit=None, scale=100,
                    threshold_db=None, **_):
    valid = SAR_VALID
    composite = FakeImage(grid(SHAPE), valid, "composite")
    mask = FakeImage(_water(start_date), valid, "water_mask")
    info = {
        "sensor": "sentinel-1",
        "collection": sar.S1_COLLECTION,
        "polarisation": "VV+VH",
        "fusion": "mean of VV and VH",
        "degraded": None,
        "scene_count": 3,
        "threshold": sar.DEFAULT_DB if threshold_db is None else float(threshold_db),
        "threshold_unit": "dB",
        "threshold_source": "fixed_validated" if threshold_db is None else "fixed",
        "threshold_raw": None,
        "validation": dict(sar.VALIDATION) if threshold_db is None else None,
    }
    return mask, composite, info


def fake_optical_detect(region, start_date, end_date, cloud_limit=40, threshold=None):
    valid = S2_POST_VALID if start_date == POST[0] else S2_PRE_VALID
    water = FakeImage(_water(start_date) & valid, valid, "water_mask")
    image = FakeImage(grid(SHAPE), valid, "composite")
    info = {
        "sensor": "sentinel-2",
        "collection": optical.S2_COLLECTION,
        "index": optical.INDEX,
        "scene_count": 4,
        "scenes_available": 6,
        "cloud_limit": cloud_limit,
        "threshold": optical.THRESHOLD if threshold is None else float(threshold),
        "threshold_unit": "MNDWI",
        "threshold_source": "fixed_shipped" if threshold is None else "fixed",
        "validation": (dict(optical.VALIDATION)
                       if (optical.VALIDATION and threshold is None) else None),
        "composite": "median of cloud-masked scenes over window",
    }
    return FakeImage(water.data, valid, "water_mask"), FakeImage(valid), image, info


def fake_zone_extract(mask, region, scale=100, simplify_m=None, area_km2=None,
                      region_area_km2=None):
    return {
        "zones": [{
            "id": "Z1", "rank": 1, "area_km2": 64.0, "centroid": [76.05, 10.05],
            "bbox": [76.0, 10.0, 76.1, 10.1], "severity": "moderate",
        }],
        "total_count": 1, "listed_count": 1, "truncated": False,
        "total_area_km2": 64.0, "listed_area_km2": 64.0,
    }


def install(monkeypatch, clear_optical=True):
    """Point every Earth Engine touchpoint in the flood path at the fakes."""
    monkeypatch.setattr(analysis, "_initialize", lambda: None)
    monkeypatch.setattr(analysis, "area_km2", fake_area_km2)
    monkeypatch.setattr(analysis, "permanent_water_mask",
                        lambda region: FakeImage(PERMANENT, name="permanent_water"))

    monkeypatch.setattr(sar, "get_collection", lambda *a, **k: FakeCollection(3))
    monkeypatch.setattr(sar, "dominant_orbit", lambda collection: Info(63))
    monkeypatch.setattr(sar, "detect_water", fake_sar_detect)

    if clear_optical:
        monkeypatch.setattr(optical, "detect_water", fake_optical_detect)

    # Population needs real Earth Engine datasets. Off by default so the flood
    # path's golden output is unchanged; tests that need it patch it back on.
    from geo import population
    monkeypatch.setattr(population, "exposure", lambda *a, **k: None)
    from geo import districts
    monkeypatch.setattr(districts, "breakdown", lambda *a, **k: None)

    monkeypatch.setattr(zone_extraction, "extract", fake_zone_extract)
    monkeypatch.setattr(zone_extraction, "to_geojson",
                        lambda zones, kind="polygon": {"type": "FeatureCollection",
                                                       "features": []})
    return FakeRegion(SHAPE)


def run(monkeypatch, sensor, baseline=True, **kwargs):
    region = install(monkeypatch, **{k: v for k, v in kwargs.items()
                                     if k == "clear_optical"})
    extra = {k: v for k, v in kwargs.items() if k != "clear_optical"}
    return analysis.analyse_flood(
        region_geometry=region,
        region_meta=dict(META),
        post_start=POST[0], post_end=POST[1],
        pre_start=PRE[0] if baseline else None,
        pre_end=PRE[1] if baseline else None,
        force_sensor=sensor,
        scale=100,
        **extra,
    )


def comparable(result):
    """The result minus what legitimately differs run to run."""
    return {k: v for k, v in result.items() if k not in ("generated_at", "_internal")}
