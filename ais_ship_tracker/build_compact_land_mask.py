#!/usr/bin/env python3
"""Convert global-land-mask's 980 MB array into small compressed row bands."""

import argparse
import json
import zipfile

import numpy as np
from global_land_mask import globe


def build(output, band_rows=120):
    ocean_mask = globe._mask
    metadata = {
        "format": 1,
        "rows": int(ocean_mask.shape[0]),
        "columns": int(ocean_mask.shape[1]),
        "band_rows": int(band_rows),
        "latitude_start": float(globe._lat[0]),
        "latitude_step": float(globe._lat[1] - globe._lat[0]),
        "longitude_start": float(globe._lon[0]),
        "longitude_step": float(globe._lon[1] - globe._lon[0]),
        "bit_value": "1=ocean,0=land",
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, separators=(",", ":")))
        for start in range(0, ocean_mask.shape[0], band_rows):
            band = np.ascontiguousarray(ocean_mask[start:start + band_rows])
            packed = np.packbits(band, axis=None, bitorder="big")
            archive.writestr("bands/%03d.bin" % (start // band_rows), packed.tobytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--band-rows", type=int, default=120)
    arguments = parser.parse_args()
    build(arguments.output, arguments.band_rows)
