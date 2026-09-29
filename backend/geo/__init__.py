"""
Places and shapes: deciding WHERE an analysis runs, and how it is drawn.

    regions    names resolved through FAO GAUL 2015, with the aliases and
               ambiguity refusals that vintage needs
    footprint  areas the user supplies instead - a box, a circle, a polygon
    indices    spectral indices on Sentinel-2 (NDVI, MNDWI, NDBI, ...)
    mapping    display hints: which layer leads, how confident it should look
    zones      contiguous detected regions, vectorised and ranked

regions and footprint answer the same question two ways, and the one rule
across both is that the answer must say which was used. A drawn rectangle
labelled as a district is a lie every downstream check would pass.
"""
