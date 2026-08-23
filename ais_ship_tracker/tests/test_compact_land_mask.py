import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from compact_land_mask import CompactLandMask


class CompactLandMaskTests(unittest.TestCase):
    def test_reads_only_the_requested_compressed_band(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "mask.zip"
            metadata = {
                "rows": 2,
                "columns": 8,
                "band_rows": 1,
                "latitude_start": 90,
                "latitude_step": -180,
                "longitude_start": -180,
                "longitude_step": 45,
            }
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("metadata.json", json.dumps(metadata))
                archive.writestr("bands/000.bin", bytes([0xFF]))
                archive.writestr("bands/001.bin", bytes([0xF7]))
            mask = CompactLandMask(archive_path)
            self.assertFalse(mask.is_land(90, 0))
            self.assertTrue(mask.is_land(-90, 0))
            self.assertEqual(mask.cache_info().currsize, 2)

    def test_rejects_invalid_coordinates(self):
        with self.assertRaises(FileNotFoundError):
            CompactLandMask("/does/not/exist.zip")


if __name__ == "__main__":
    unittest.main()
