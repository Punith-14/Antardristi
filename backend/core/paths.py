"""
Where everything lives, defined once.

Modules used to work out their own paths with `Path(__file__).parent.parent`,
which encodes how deep in the tree the file happens to sit. Nine modules did
this, and moving any of them one level changed what `parent.parent` meant -
silently, because a wrong path does not raise until something tries to read it.
classifier.py would have started looking for the trained model inside backend/
instead of alongside it, and the first sign would have been "model not found"
at request time.

So the depth is computed once, here, and everything else imports a name.
"""

from pathlib import Path

# core/paths.py -> core/ -> backend/ -> antardrishti/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

BACKEND = PROJECT_ROOT / "backend"

# The worked example every workstream builds against.
CONTRACTS = PROJECT_ROOT / "contracts"

# Cached analyses, uploaded images, downloaded imagery.
DATA = PROJECT_ROOT / "data"
CACHE = DATA / "cache"
RAW = DATA / "raw"

# Benchmarks, measured scores, sampled training data. Tracked in git: these
# are the numbers the report cites.
EVALUATION = PROJECT_ROOT / "evaluation"

# Trained models. The .pkl binaries are gitignored; the .json metadata beside
# them is not, because it records what each model was trained on and scored.
MODELS = PROJECT_ROOT / "models"

# Rendered PNGs and GeoJSON, served at /outputs.
OUTPUTS = PROJECT_ROOT / "outputs"

# Output of the scripts in backend/scripts - diagnostic runs and validation
# sweeps. Separate from evaluation/ because these are working notes rather
# than results the report leans on.
RESULTS = BACKEND / "results"
