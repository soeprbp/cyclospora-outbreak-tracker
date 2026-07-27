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

    def test_state_trackers(self):
        illinois = "* Cyclospora In Illinois: 513 Confirmed and Probable Cases 291 Domestically Acquired *Data is as of 7/23/26 at 7am"
        indiana = "Indiana Case Counts Total Cases: 710 Last updated: July 24 Data notes"
        new_york = "Total Cases for 2026, 1/1/2026 - 7/20/2026: 666 Last Updated: 7/22/2026"
        wisconsin = "2026 Cyclospora season– Updated July 22, 2026 As of July 22, there have been 105 cases of cyclosporiasis reported in Wisconsin during this year's Cyclospora season so far, including three hospitalizations."
        self.assertEqual(module.parse_idph(illinois)["cases"], 513)
        self.assertEqual(module.parse_idoh(indiana)["official_as_of"], "2026-07-24")
        self.assertEqual(module.parse_nysdoh(new_york)["cases"], 666)
        self.assertEqual(module.parse_widhs(wisconsin)["hospitalizations"], 3)

    def test_cdc_revised_domestic_section(self):
        raw = "Cases acquired in the U.S. May 1 - July 20, 2026: Cases 4,173 Hospitalizations 308 Deaths 0 States reporting cases 41 These people became sick. Cases acquired outside the U.S."
        parsed = module.parse_cdc(raw)
        self.assertEqual(parsed["official_as_of"], "2026-07-20")
        self.assertEqual(parsed["cases"], 4173)
        self.assertEqual(parsed["hospitalizations"], 308)
        self.assertEqual(parsed["states"], 41)

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
            published["sources"]["nysdoh"]["cases"],
        )

    def test_older_state_source_does_not_override_newer_nndss(self):
        sources = {
            "nndss": {
                "official_as_of": "2026-07-18",
                "jurisdictions": {"NJ": {"cases": 103}},
            },
            "nysdoh": {"official_as_of": "2026-07-20", "cases": 666},
        }
        state_data = module.build_state_data(sources)
        self.assertEqual(state_data["NJ"]["cases"], 103)
        self.assertEqual(state_data["NY"]["cases"], 666)

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
