"""Bounded-memory reader for the Baiamonte AIS global land mask.

The source GLOBE mask is split into independently compressed latitude bands at
image-build time.  Runtime lookups inflate only the few bands around active AIS
targets instead of expanding the original 980 MB NumPy array.
"""

import json
import threading
import zipfile
from functools import lru_cache
from pathlib import Path


class CompactLandMask:
    """Read individual land/ocean bits from a banded ZIP archive."""

    def __init__(self, archive_path):
        self.path = Path(archive_path)
        self._archive = zipfile.ZipFile(self.path, "r")
        self._lock = threading.Lock()
        metadata = json.loads(self._archive.read("metadata.json"))
        self.rows = int(metadata["rows"])
        self.columns = int(metadata["columns"])
        self.band_rows = int(metadata["band_rows"])
        self.latitude_start = float(metadata["latitude_start"])
        self.latitude_step = float(metadata["latitude_step"])
        self.longitude_start = float(metadata["longitude_start"])
        self.longitude_step = float(metadata["longitude_step"])

    @staticmethod
    def _coordinate_index(value, start, step, size):
        index = int((float(value) - start) / step)
        return max(0, min(size - 1, index))

    @lru_cache(maxsize=8)
    def _band(self, band_number):
        with self._lock:
            return self._archive.read("bands/%03d.bin" % band_number)

    def is_land(self, latitude, longitude):
        if not -90 <= float(latitude) <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= float(longitude) <= 180:
            raise ValueError("longitude must be between -180 and 180")
        row = self._coordinate_index(latitude, self.latitude_start, self.latitude_step, self.rows)
        column = self._coordinate_index(longitude, self.longitude_start, self.longitude_step, self.columns)
        band_number, band_row = divmod(row, self.band_rows)
        bit_index = band_row * self.columns + column
        packed = self._band(band_number)
        ocean = bool(packed[bit_index // 8] & (1 << (7 - bit_index % 8)))
        return not ocean

    def cache_info(self):
        return self._band.cache_info()
