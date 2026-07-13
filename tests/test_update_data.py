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

    def test_published_state_data_tracks_nndss(self):
        published = __import__("json").loads((Path(__file__).parents[1] / "data" / "outbreak.json").read_text(encoding="utf-8"))
        self.assertEqual(published["schema_version"], 2)
        self.assertEqual(published["state_data"]["MI"]["source"], "nndss")
        self.assertEqual(published["state_data"]["NY"]["cases"], 460)

    def test_nndss_jurisdictions_and_flags(self):
        raw = (Path(__file__).parent / "fixtures" / "nndss.html").read_text(encoding="utf-8")
        parsed = module.parse_nndss(raw)
        self.assertEqual(parsed["official_as_of"], "2026-07-04")
        self.assertEqual(parsed["jurisdictions"]["NY"]["cases"], 460)
        self.assertEqual(parsed["jurisdictions"]["NY"]["components"]["nyc"], 343)
        self.assertEqual(parsed["jurisdictions"]["VT"]["cases"], 0)
        self.assertEqual(parsed["jurisdictions"]["PA"]["status"], "not-reportable")
        self.assertEqual(parsed["us_residents_total"], 1838)


if __name__ == "__main__": unittest.main()
