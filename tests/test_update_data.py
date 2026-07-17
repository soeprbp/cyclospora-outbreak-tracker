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

    def test_state_data_retains_comparable_and_newer_official_totals(self):
        published = __import__("json").loads((Path(__file__).parents[1] / "data" / "outbreak.json").read_text(encoding="utf-8"))
        self.assertEqual(published["schema_version"], 2)
        state_data = module.build_state_data(published["sources"])
        self.assertEqual(state_data["MI"]["source"], "Michigan MDHHS")
        self.assertEqual(state_data["MI"]["cases"], published["sources"]["mdhhs"]["cases"])
        self.assertEqual(
            state_data["MI"]["comparable_cases"],
            published["sources"]["nndss"]["jurisdictions"]["MI"]["cases"],
        )
        self.assertEqual(
            published["state_data"]["NY"]["cases"],
            published["sources"]["nndss"]["jurisdictions"]["NY"]["cases"],
        )

    def test_nndss_api_uses_latest_week_for_all_rows(self):
        raw = '[{"states":"U.S. Residents","year":"2026","week":"26","label":"Cyclosporiasis","m3":"10"},{"states":"Michigan","year":"2026","week":"26","label":"Cyclosporiasis","m3":"4"}]'
        with self.assertRaises(ValueError):
            module.parse_nndss(raw)

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
