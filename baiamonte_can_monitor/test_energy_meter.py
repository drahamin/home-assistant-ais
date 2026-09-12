import json
import tempfile
import unittest
from pathlib import Path

from energy_meter import EnergyMeter


class EnergyMeterTests(unittest.TestCase):
    def test_integrates_charge_and_discharge_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            meter = EnergyMeter(Path(directory) / "energy.json", max_gap_s=4000)
            meter.update(1000, now=10)
            meter.update(1000, now=3610)
            meter.update(-1000, now=7210)
            self.assertEqual(meter.charged_kwh, 1.0)
            self.assertEqual(meter.discharged_kwh, 0.0)

            # A same-sign interval avoids assigning a direction across a zero crossing.
            meter.update(-1000, now=10810)
            self.assertEqual(meter.discharged_kwh, 1.0)

    def test_ignores_restart_gaps_and_idle_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            meter = EnergyMeter(Path(directory) / "energy.json", max_gap_s=30)
            meter.update(4, now=0)
            meter.update(4, now=20)
            meter.update(1000, now=1000)
            self.assertEqual(meter.charged_kwh, 0.0)

    def test_persists_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "energy.json"
            path.write_text(json.dumps({"charged_kwh": 12.5, "discharged_kwh": 8.25}))
            meter = EnergyMeter(path)
            self.assertEqual(meter.readings()["bank_energy_charged"].value, 12.5)
            meter.save()
            restored = EnergyMeter(path)
            self.assertEqual(restored.discharged_kwh, 8.25)
            self.assertEqual(restored.readings()["bank_energy_charged"].state_class, "total_increasing")


if __name__ == "__main__":
    unittest.main()
