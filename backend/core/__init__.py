"""
Infrastructure the other layers lean on, and that leans on nothing of ours.

    paths          where everything lives, computed once
    earth_engine   one initialiser, idempotent
    cache          analyses keyed by request, so a repeat costs nothing
    evidence       the evidence record - every number with its method, units,
                   provenance, confidence and spatial support
    verification   checks a generated report against the evidence record

Nothing here imports from geo, detection or pipeline. If something in core
needs one of those, it belongs in that layer instead.
"""
