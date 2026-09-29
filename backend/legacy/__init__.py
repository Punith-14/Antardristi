"""
The pre-evidence-record path, still wired to /query and the upload endpoint.

    gee_fetch     optical water analysis, returns rendered PNGs
    ndwi          uploaded-image screening on RGB
    query_parser  keyword intent matching, superseded by pipeline.routing

Kept because those endpoints still work and are demonstrated. Not extended.
Anything here that needs to survive should be rebuilt to emit an evidence
record first - without one, a number cannot be checked, and checking every
number is the point of the project.
"""
