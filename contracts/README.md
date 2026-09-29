# Response contract

`analysis_response.json` is the single agreed shape of an Antardrishti analysis result.
Everyone builds against it, so nobody waits for anyone else.

- **Frontend** renders this file directly as a fixture. No backend needed to start.
- **Backend** must produce exactly this structure.
- **Evaluation** scores against `evidence` and `verification`.

If you need to change it, change it here first and tell the other two.

---

## The rule that matters

**The report generator receives `evidence` and nothing else.**

Not pixels, not the raw Earth Engine response, not the user's question history. Only
the evidence array. Every quantitative statement in `report.text` must cite an
evidence id in square brackets, and `verification` then checks that each number in
the prose actually appears in `evidence` with a matching unit.

This is the project's contribution. If the generator is ever given a second source of
information, the faithfulness metric becomes meaningless and the claim collapses.

---

## Section notes

**`observation` vs `unobserved`** — every result declares how much of the region was
actually seen. `coverage_fraction` below ~0.9 means the headline number is computed
from a fraction of the area and the UI must say so. This is not decoration: our own
Kerala test measured coverage collapsing from 37,575 km2 to 6,820 km2 during a flood,
while the reported water percentage went *down*. A result without its coverage is
misleading.

**`evidence[].denominator`** — percentages are of *observable* area, never total
region area. State which, always.

**`evidence[].derived_from`** — values computed from other values reference their
inputs, so any number can be traced back to a measurement.

**`zones`** — ranked largest first. A single district-wide percentage is not
actionable; discrete located zones are.

**`report.fallback_used`** — true when the template generator produced the text
because the LLM was unavailable. The template path takes only the evidence array and
is also the ablation baseline for measuring whether the LLM adds anything.

**`provenance.known_confusions`** — failure modes travel *with* the result, not
buried in a limitations section. What the system cannot distinguish is as important
as what it detected.

**`provenance.models`** — empty in this example because the flood path is a
threshold, not a model. When a model is used, each entry carries its id, version and
test score, e.g. `{"id": "landcover_rf_v1", "version": "1.0", "metric": "mIoU",
"score": 0.58, "dataset": "DeepGlobe test"}`.

---

## Null cases

Two failure shapes the frontend must handle:

1. **No usable imagery.** `evidence` is empty, `unobserved.reason` is
   `"no_usable_imagery"`, `report.text` explains why. Implemented already in
   `main.py`.
2. **Partial coverage.** Evidence is present but `coverage_fraction` is low. Render
   the numbers *with* a prominent coverage warning rather than suppressing them.

Never return `0.0` when the answer is "could not observe". They are different facts.

---

## Versioning

`schema_version` is bumped on any breaking change. Fixtures under `contracts/` are
the source of truth for tests on all three sides.
