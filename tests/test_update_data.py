import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("update_data", Path(__file__).parents[1] / "scripts" / "update_data.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ParserTests(unittest.TestCase):
    def test_mdhhs(self):
        raw = "MDHHS is investigating an outbreak of cyclosporiasis. Michigan Case Counts Total Cases: 1,562 To date, 44 reported cases indicated they had been hospitalized. Last updated: July 10, 2026"
        self.assertEqual(module.parse_mdhhs(raw)["cases"], 1562)

    def test_cdc(self):
        raw = "2026 fast facts As of July 9, 2026: U.S. cases reported to CDC: 843 Hospitalizations: 86 Deaths: 0 States reporting cases: 31 Overview"
        self.assertEqual(module.parse_cdc(raw)["states"], 31)

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            module.parse_mdhhs("MDHHS is investigating an outbreak of cyclosporiasis Total Cases: 10 To date, 44 reported cases indicated they had been hospitalized. Last updated: July 10, 2026")


if __name__ == "__main__": unittest.main()
