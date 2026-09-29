"""
Shared fixtures.

Modules live in packages under backend/ (core, geo, detection, pipeline),
so backend/ goes on sys.path and imports read `from detection import sar`.
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
CONTRACTS = BACKEND.parent / "contracts"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Load .env so the marked tests can find GROQ_API_KEY and EE_PROJECT_ID.
# Without this, `pytest -m llm` silently skips even when the key is configured -
# a skipped test reads like a passing one, which is worse than a failure.
try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env")
except ImportError:
    pass


@pytest.fixture(scope="session")
def contract():
    """The worked example every workstream builds against."""
    return json.loads((CONTRACTS / "analysis_response.json").read_text(encoding="utf-8"))


@pytest.fixture
def evidence(contract):
    return contract["evidence"]


@pytest.fixture
def flood_payload():
    """A minimal but realistic flood response, built by hand.

    Deliberately not the contract fixture: this one is small enough to reason
    about, so a failing assertion points at one value.
    """
    return {
        "region": {"name": "Kendrapara", "area_km2": 2644.0},
        "observation": {
            "sensor_used": "sentinel-1",
            "observable_area_km2": 2598.0,
            "coverage_fraction": 0.983,
            "scenes_used": 6,
            "scenes_available": 14,
        },
        "unobserved": {"masked_area_km2": 46.0, "notes": []},
        "evidence": [
            {
                "id": "E1",
                "quantity": "flood_extent",
                "value": 187.4,
                "unit": "km2",
                "threshold": -20.0,
                "method": "Sentinel-1 VV+VH at -20.0 dB",
            },
            {
                "id": "E2",
                "quantity": "flood_extent_fraction",
                "value": 7.21,
                "unit": "percent",
                "derived_from": ["E1"],
            },
        ],
        "zones": [
            {
                "id": "Z1",
                "rank": 1,
                "area_km2": 42.1,
                "centroid": [86.42, 20.51],
                "severity": "high",
            }
        ],
    }
