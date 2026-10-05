"""
The satellites behind the data: where each Sentinel-1 is right now, what ESA
plans it to image next, and the newest image Earth Engine already holds.

    celestrak.py  orbital elements (CelesTrak GP data), cached, refreshed every 2 h
    plans.py      ESA's published acquisition plans (KML), parsed and intersected
    latest.py     the newest Sentinel-1 scene over an area, from Earth Engine
    service.py    puts the three together for the API

Nothing here is estimated or invented. Positions are propagated from published
elements (SGP4, in the browser); planned images come from ESA's own plan; the
last image is a real scene ID. When a source cannot be reached the answer says
so and, where it has one, serves the last good copy marked as stale.
"""
