"""
List or cancel Earth Engine export tasks.

Written to stop the 16 green-cover exports after notebooks 10 and 11 were
dropped: they keep running on Google's side and compete with the app for the
project's compute while it is in restricted mode.

Run (from backend/):
    python -m scripts.cancel_exports            # list tasks, cancel nothing
    python -m scripts.cancel_exports --cancel   # cancel queued/running green_ and crop_stress_ tasks
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

import ee  # noqa: E402

from core import earth_engine  # noqa: E402

PREFIXES = ("green_", "crop_stress_")
ACTIVE = {"PENDING", "RUNNING", "READY"}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--cancel", action="store_true", help="cancel the matching active tasks")
    args = parser.parse_args()

    earth_engine.initialize()
    ops = ee.data.listOperations()
    if not ops:
        print("No export tasks in this project.")
        return
    cancelled = 0
    for op in ops:
        meta = op.get("metadata", {})
        name, state = meta.get("description", "?"), meta.get("state", "?")
        ours = name.startswith(PREFIXES)
        print(f"{state:<10} {name}")
        if args.cancel and ours and state in ACTIVE:
            ee.data.cancelOperation(op["name"])
            print("           -> cancelled")
            cancelled += 1
    if args.cancel:
        print(f"\nCancelled {cancelled} task(s).")
    else:
        print("\nNothing cancelled. Add --cancel to stop the green_/crop_stress_ tasks.")


if __name__ == "__main__":
    main()
