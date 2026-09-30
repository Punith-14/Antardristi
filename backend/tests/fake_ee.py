"""
A stand-in for the few Earth Engine objects the flood path touches.

The flood path is the core of the project and could not be run in a test at
all: every line of it is an ee.Image operation. So nothing checked that the
SAR and optical branches emit the same quantities, or that a refactor left
the SAR numbers alone.

This replaces an ee.Image with a numpy boolean grid plus a validity grid.
Each pixel is one square kilometre, so areas are pixel counts and every
expected number in a test can be worked out by hand.

Only the operations analysis.py actually calls are implemented. If the
production code starts calling something new, the test fails with an
AttributeError naming it - which is the right failure: it means the fake no
longer describes what is being tested.
"""

import numpy as np

PIXEL_KM2 = 1.0


class Info:
    """Anything with .getInfo()."""

    def __init__(self, value):
        self._value = value

    def getInfo(self):
        return self._value


class FakeImage:
    """A boolean raster with a validity mask, supporting ee-style algebra.

    ee semantics kept where they matter: combining two images yields a pixel
    that is valid only where both inputs are valid, and area is counted only
    over valid pixels that are 1.
    """

    def __init__(self, data, valid=None, name="image"):
        self.data = np.asarray(data, dtype=bool)
        self.valid = (np.ones_like(self.data) if valid is None
                      else np.asarray(valid, dtype=bool))
        self.name = name

    # --- algebra -----------------------------------------------------------
    def And(self, other):
        return FakeImage(self.data & other.data, self.valid & other.valid)

    def Or(self, other):
        return FakeImage(self.data | other.data, self.valid & other.valid)

    def Not(self):
        return FakeImage(~self.data, self.valid)

    def rename(self, name):
        return FakeImage(self.data, self.valid, name)

    def mask(self):
        """ee's mask(): 1 where the pixel has data, as a fully valid image."""
        return FakeImage(self.valid, np.ones_like(self.valid))

    def selfMask(self):
        return FakeImage(self.data, self.valid & self.data)

    def updateMask(self, other):
        return FakeImage(self.data, self.valid & other.data)

    def unmask(self, value=0):
        """ee's unmask(v): masked pixels become v and the image is fully valid."""
        filled = np.where(self.valid, self.data, bool(value))
        return FakeImage(filled, np.ones_like(self.valid))

    def visualize(self, **_):
        return self

    def clip(self, _):
        return self

    # --- measurement -------------------------------------------------------
    def area_km2(self):
        return float((self.data & self.valid).sum()) * PIXEL_KM2


class FakeFloat:
    """A float raster (backscatter in dB, say) with a validity mask.

    Comparisons give a FakeImage; arithmetic gives a FakeFloat. As in Earth
    Engine, the result of combining two images is valid only where both are.
    """

    def __init__(self, data, valid=None, name="float"):
        self.data = np.asarray(data, dtype=np.float64)
        self.valid = (np.isfinite(self.data) if valid is None
                      else np.asarray(valid, dtype=bool) & np.isfinite(self.data))
        self.name = name

    def _other(self, other):
        if isinstance(other, FakeFloat):
            return other.data, other.valid
        return float(other), np.ones_like(self.valid)

    def lt(self, other):
        data, valid = self._other(other)
        with np.errstate(invalid="ignore"):
            return FakeImage(self.data < data, self.valid & valid)

    def gt(self, other):
        data, valid = self._other(other)
        with np.errstate(invalid="ignore"):
            return FakeImage(self.data > data, self.valid & valid)

    def subtract(self, other):
        data, valid = self._other(other)
        return FakeFloat(self.data - data, self.valid & valid)

    def mask(self):
        return FakeImage(self.valid, np.ones_like(self.valid))

    def rename(self, name):
        return FakeFloat(self.data, self.valid, name)

    def visualize(self, **_):
        return self


class FakeRegion:
    def __init__(self, shape):
        self.shape = shape

    def area(self, maxError=None):
        return Info(self.shape[0] * self.shape[1] * PIXEL_KM2 * 1_000_000)


class FakeCollection:
    def __init__(self, size):
        self._size = size

    def size(self):
        return Info(self._size)


def fake_area_km2(mask, region, scale=100):
    """Drop-in for analysis.area_km2."""
    return mask.area_km2()


def grid(shape, *boxes, value=True):
    """A boolean grid with rectangular boxes set: (row0, col0, row1, col1)."""
    array = np.zeros(shape, dtype=bool) if value else np.ones(shape, dtype=bool)
    for r0, c0, r1, c1 in boxes:
        array[r0:r1, c0:c1] = value
    return array
