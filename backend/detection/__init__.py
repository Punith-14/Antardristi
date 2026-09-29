"""
Turning imagery into masks - the measurement itself.

    sar         Sentinel-1 flood detection, mean(VV,VH) at -20 dB
    surface     six optical analyses over Sentinel-2
    classifier  the land-cover Random Forest, serialised into Earth Engine

Every detector here carries its own measured accuracy and its own known
confusions, so both travel with the result rather than living in a
limitations section nobody reads.
"""
