"""
One place that starts Earth Engine.

There were three copies of this before - in analysis.py, regions.py and nearly
a fourth in footprint.py - and the fourth is what surfaced the problem: a new
code path reached ee.Geometry without passing through any of the existing
initialisers and died with "Earth Engine client library not initialized" at
request time rather than at startup.

Duplicated setup fails that way by nature. Every new entry point has to
remember to call its own copy, and forgetting is invisible until the request
that needs it.
"""

import os

import ee

_ready = False


def initialize(force=False):
    """Start Earth Engine once per process.

    Idempotent: ee.Initialize does a network round trip to fetch the algorithm
    list, and calling it on every request adds that latency to every analysis.
    """
    global _ready
    if _ready and not force:
        return

    try:
        project_id = os.environ.get("EE_PROJECT_ID")
        if project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
    except Exception as exc:
        raise RuntimeError(
            "Earth Engine is not authenticated. Run `earthengine authenticate` "
            "or `ee.Authenticate()`, and set EE_PROJECT_ID in .env."
        ) from exc

    _ready = True


def is_ready():
    return _ready
