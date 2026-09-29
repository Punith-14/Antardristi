"""
How much does the classifier cost, and is it worth it?

The model runs inside Earth Engine, which means every tree is shipped there as
text. A 300-tree forest with unlimited depth can serialise to megabytes, and
that is paid on the first query of every process.

This measures the size, then times a real query both ways.

Run:  python -m scripts.diagnose_classifier_cost
"""

import time

from dotenv import load_dotenv

load_dotenv()

import ee

from pipeline import analysis
from detection import classifier
from detection import surface

REGION = "bangalore urban"
WINDOW = ("2023-01-01", "2023-03-31")


def main():
    analysis._initialize()

    print("=" * 66)
    print("SERIALISED FOREST")
    print("=" * 66)

    started = time.time()
    stats = classifier.tree_stats()
    elapsed = time.time() - started

    print(f"  trees           {stats['trees']:>10,}")
    print(f"  total nodes     {stats['total_nodes']:>10,}")
    print(f"  serialised      {stats['serialised_kb']:>10,.1f} KB")
    print(f"  build time      {elapsed:>10.2f} s  (once per process, then cached)")

    if stats["serialised_kb"] > 2000:
        print("\n  This is large. Retraining with max_depth=12 and 100 trees")
        print("  would cut it substantially, usually for little accuracy.")

    geometry, meta = analysis.resolve_geometry(REGION)
    if geometry is None:
        print(f"\n{REGION} did not resolve.")
        return

    print("\n" + "=" * 66)
    print(f"REAL QUERY: built-up over {meta['name']}")
    print("=" * 66)

    for label, force_index in (("index threshold", True), ("classifier", False)):
        config = surface.ANALYSES["built_up"]
        saved = config.pop("classifier_class", None) if force_index else None

        started = time.time()
        try:
            result = surface.analyse(
                region_geometry=geometry,
                region_meta=meta,
                analysis_type="built_up",
                post_start=WINDOW[0],
                post_end=WINDOW[1],
                scale=200,
                include_zones=False,
            )
            elapsed = time.time() - started
            area = next(
                (e["value"] for e in result["evidence"]
                 if e["quantity"] == "built_up_area"),
                None,
            )
            method = result["method"]["used"]
            print(f"  {label:<18}{elapsed:>7.1f} s   {area:>10,.1f} km2   [{method}]")
        except Exception as exc:
            print(f"  {label:<18}  failed: {str(exc)[:60]}")
        finally:
            if saved:
                config["classifier_class"] = saved

    print("\n  Cached responses return in ~0.05 s either way, so this cost is")
    print("  paid once per unique query, not per view.")


if __name__ == "__main__":
    main()
