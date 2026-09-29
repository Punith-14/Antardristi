"""
Run by hand, never imported by the service.

Measurement and diagnosis: threshold sweeps, boundary coverage, routing
benchmarks, model training, Earth Engine cost probes. Several write into
evaluation/ or results/, and the report cites what they produce.

Run as modules from backend/, so imports resolve:

    python -m scripts.measure_boundary_coverage
    python -m scripts.train_landcover --trees 60 --depth 10
"""
