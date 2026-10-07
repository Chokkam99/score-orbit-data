#!/usr/bin/env python3
"""Self-tests for names_report.py. Standard library only.

Run from the repo root:  python3 -m unittest discover -s tests -v

The end-to-end tests copy names_report.py into a temp directory with small hand-made data files and
an app-style fixtures/ directory, so they never read the repo's data or an app checkout.
"""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import names_report  # noqa: E402
import validate  # noqa: E402


def feed(*events, names=None):
    doc = {"schemaVersion": 1, "updatedOn": "2026-10-06", "sport": "BADMINTON", "events": list(events)}
    if names is not None:
        doc["names"] = names
    return doc


def event(sport, *entries):
    return {"id": "x", "sport": sport, "entries": list(entries)}


def entry(country, athletes, opponents=(), opponent_country="CHN"):
    matches = [{"opponent": list(o), "opponentCountry": opponent_country} for o in opponents]
    return {"country": country, "athletes": list(athletes), "matches": matches}


class Report:
    """A temp directory holding names_report.py, results.json, upcoming.json and a fixtures/ directory."""

    def __init__(self, test, results, upcoming=None):
        self.dir = Path(tempfile.mkdtemp(prefix="scoreorbit-names-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copy(REPO / "names_report.py", self.dir)
        (self.dir / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (self.dir / "upcoming.json").write_text(json.dumps(upcoming or feed()), encoding="utf-8")
        self.fixtures = self.dir / "app" / "fixtures"
        self.fixtures.mkdir(parents=True)

    def csv(self, name, header, rows):
        with (self.fixtures / name).open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    def run(self, *args):
        done = subprocess.run([sys.executable, "names_report.py", *args], cwd=self.dir, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr


class EndToEndTest(unittest.TestCase):
    def make(self):
        results = feed(
            event("BADMINTON",
                  entry("IND", ["Tanvi Sharma"], opponents=[["Li Shifeng"]]),
                  entry("IND", ["Srikanth Kidambi"]),
                  entry("IND", ["Akhil Reddy Bobba"]),
                  entry("IND", ["P. V. Sindhu"])),
            event("TABLE_TENNIS", entry("SGP", ["Zeng Jian"], opponents=[["U Tae-ryong"]], opponent_country="PRK")),
            names=[{"sport": "BADMINTON", "country": "IND", "name": "P. V. Sindhu", "aliases": ["Pusarla V. Sindhu"]}],
        )
        upcoming = feed(event("TABLE_TENNIS", entry("SGP", ["Jian Zeng"])))
        r = Report(self, results, upcoming)
        r.csv("senior-draw.csv", ["discipline", "draw_slot", "athlete_1", "member_id_1", "country_1",
                                  "athlete_2", "member_id_2", "country_2", "pdf_page"],
              [["WS", "1", "T SHARMA", "1", "IND", "", "", "", "1"],
               ["WS", "2", "Pusarla V. Sindhu", "2", "IND", "", "", "", "1"],
               ["MS", "3", "Kidambi Srikanth", "3", "IND", "", "", "", "1"]])
        r.csv("junior-draw.csv", ["discipline", "entry_country", "athlete_1", "player_id_1", "athlete_2", "player_id_2"],
              [["MD", "IND", "A. BOBBA", "4", "Y. SINGH", "5"]])
        metadata = [
            {"sport": "BADMINTON", "csv": "senior-draw.csv", "progression": {"WS:1": [
                {"opponent": "Li Shi Feng", "opponentCountry": "CHN"},
                {"opponent": "D. Shetty (USA)"},
                {"opponent": "Kim Won Ho / Seo Seung Jae", "opponentCountry": "KOR"},
            ]}},
            {"sport": "BADMINTON", "csv": "junior-draw.csv", "progression": {}},
        ]
        (r.fixtures / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return r

    def test_lists_short_forms_and_near_duplicates_grouped_by_sport_and_country(self):
        code, out = self.make().run("app/fixtures")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertIn("BADMINTON CHN", lines)
        self.assertIn('  spacing "Li Shi Feng" ~ "Li Shifeng"', lines)
        self.assertIn("BADMINTON IND", lines)
        self.assertIn('  order   "Kidambi Srikanth" ~ "Srikanth Kidambi"', lines)
        self.assertIn('  short   "T SHARMA" -> "Tanvi Sharma"  (fixtures/senior-draw.csv)', lines)
        self.assertIn('  short   "A. BOBBA" -> "Akhil Reddy Bobba"  (fixtures/junior-draw.csv)', lines)
        self.assertIn("TABLE_TENNIS SGP", lines)
        self.assertIn('  order   "Jian Zeng" ~ "Zeng Jian"', lines)
        self.assertTrue(lines[0].startswith("names_report: 5 candidate(s)"), out)

    def test_directory_spellings_and_short_forms_without_a_fuller_name_are_not_listed(self):
        code, out = self.make().run("app/fixtures")
        self.assertNotIn("Sindhu", out)  # "Pusarla V. Sindhu" is an alias in the directory
        self.assertNotIn("Y. SINGH", out)  # no fuller Singh in the data
        self.assertNotIn("U Tae-ryong", out)  # "U" is a surname; nothing fuller anyway
        self.assertNotIn("Shetty", out)
        self.assertIn("(3 short form(s) have no fuller spelling in the data and are not listed)", out)

    def test_without_fixtures_only_the_data_files_are_read(self):
        code, out = self.make().run()
        self.assertEqual(code, 0, out)
        self.assertIn("(read results.json, upcoming.json)", out)
        self.assertIn('"Jian Zeng" ~ "Zeng Jian"', out)
        self.assertNotIn("T SHARMA", out)

    def test_a_missing_fixtures_directory_is_a_note_not_a_failure(self):
        code, out = self.make().run("no/such/fixtures")
        self.assertEqual(code, 0, out)
        self.assertIn("note: no/such/fixtures is not a directory; reporting on the data files only", out)
        self.assertIn('"Jian Zeng" ~ "Zeng Jian"', out)

    def test_two_possible_full_names_are_both_listed(self):
        r = Report(self, feed(event("BADMINTON", entry("IND", ["Tanvi Sharma"]), entry("IND", ["Tara Sharma"]),
                                    entry("IND", ["T. Sharma"]))))
        code, out = r.run()
        self.assertIn('  short   "T. Sharma" -> "Tanvi Sharma", "Tara Sharma"  (results.json)', out.splitlines())

    def test_a_hyphen_for_a_space_is_listed_but_case_dots_and_accents_are_not(self):
        r = Report(self, feed(event("BADMINTON", entry("TPE", ["Chi Yu-jen"]), entry("TPE", ["CHI Yu Jen"]),
                                    entry("TPE", ["Chi Yu Jen"]), entry("TPE", ["Chou Tien-chen"]),
                                    entry("IND", ["P.V. Sindhu"]), entry("IND", ["P. V. Sindhu"]),
                                    entry("VIE", ["Nguyen Thuy Linh"]), entry("VIE", ["Nguy\u1ec5n Th\u00f9y Linh"]))))
        code, out = r.run()
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("names_report: 1 candidate(s)"), out)
        self.assertIn('  hyphen  "Chi Yu Jen" ~ "Chi Yu-jen"', out.splitlines())
        self.assertNotIn("Sindhu", out)
        self.assertNotIn("Linh", out)

    def test_a_hyphen_pair_in_the_directory_is_not_listed(self):
        r = Report(self, feed(event("BADMINTON", entry("PRK", ["Kim Kum-yong"]), entry("PRK", ["Kim Kum Yong"])),
                              names=[{"sport": "BADMINTON", "country": "PRK", "name": "Kim Kum-yong", "aliases": ["Kim Kum Yong"]}]))
        code, out = r.run()
        self.assertTrue(out.startswith("names_report: 0 candidate(s)"), out)

    def test_a_fuller_name_in_the_directory_still_gets_its_short_forms_listed(self):
        r = Report(self, feed(event("BADMINTON", entry("IND", ["Tanvi Sharma"]), entry("IND", ["T. Sharma"])),
                              names=[{"sport": "BADMINTON", "country": "IND", "name": "Tanvi Sharma", "aliases": ["Sharma Tanvi"]}]))
        code, out = r.run()
        self.assertIn('  short   "T. Sharma" -> "Tanvi Sharma"  (results.json)', out.splitlines())

    def test_unreadable_data_fails(self):
        r = Report(self, feed())
        (r.dir / "results.json").write_text("{not json", encoding="utf-8")
        code, out = r.run()
        self.assertEqual(code, 1, out)
        self.assertIn("cannot read results.json or upcoming.json", out)


class ShapeTest(unittest.TestCase):
    """The rules ported from the app's NameResolution.kt (see NameResolutionTest there)."""

    def test_initials_and_words(self):
        self.assertEqual(names_report.initials(names_report.shape("Aathish Sreenivas PV")), ["p", "v"])
        self.assertEqual(names_report.initials(names_report.shape("M.R. Arjun")), ["m", "r"])
        self.assertEqual(names_report.words(names_report.shape("Sai Pratheek.K")), ["sai", "pratheek"])
        self.assertEqual(names_report.initials(names_report.shape("T SHARMA")), ["t"])
        self.assertEqual(names_report.initials(names_report.shape("Shifeng Li")), [])
        self.assertFalse(names_report.is_short(names_report.shape("S. R.")))

    def test_expands(self):
        def expands(fuller, shorter):
            return names_report.expands(names_report.shape(fuller), names_report.shape(shorter))
        self.assertTrue(expands("Akhil Reddy Bobba", "A. BOBBA"))
        self.assertTrue(expands("Ruthvika Gadde", "Gadde R."))
        self.assertTrue(expands("Aathish Sreenivas PV", "A. SREENIVAS P V"))
        self.assertTrue(expands("An Se-young", "An S. Y."))
        self.assertFalse(expands("Feng Yanzhe", "Feng Y. Z."))
        self.assertFalse(expands("Pruthvi Roy", "P. Krishnamurthy Roy"))
        self.assertFalse(expands("Tanvi Sharma", "R. Sharma"))

    def test_name_key_is_the_validators(self):
        for raw in ("P.V. Sindhu", "  T  SHARMA ", "Chi Yu-jen", "Lê Đức Phát", "Pratheek.K"):
            self.assertEqual(names_report.name_key(raw), validate.name_key(raw), raw)

    def test_hyphen_key_keeps_only_the_hyphen(self):
        self.assertEqual(names_report.hyphen_key("CHI Yu-Jen"), "chi yu-jen")
        self.assertEqual(names_report.hyphen_key("Chi  Yu - jen"), "chi yu-jen")
        self.assertEqual(names_report.hyphen_key("P.V. Sindhu"), names_report.hyphen_key("P. V. Sindhu"))


if __name__ == "__main__":
    unittest.main()
