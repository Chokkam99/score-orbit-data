#!/usr/bin/env python3
"""Self-tests for validate.py and heartbeat.py. Standard library only.

Run from the repo root:  python3 -m unittest discover -s tests -v

Each test builds a small valid pair of data files in a temp directory, copies validate.py and the
real rules.json next to them, breaks one thing and runs `python3 validate.py` there, then asserts
that the run fails with the expected message (and that the unbroken data passes). The tests do not
read the repo's results.json / upcoming.json, so they keep working as the data changes. The
previous-version tests make a throw-away git repository in the temp directory.
"""
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import heartbeat  # noqa: E402
import validate  # noqa: E402

# The zone-name tests need the machine's time zone database; without one validate.py checks names by shape.
HAS_TZ_DB = bool(zoneinfo.available_timezones())

TODAY = datetime.now(timezone.utc).date()
ISO = date.isoformat
GIT = shutil.which("git")
# A throw-away repo must not pick up the developer's git configuration (signing, hooks, identity).
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
GIT_FLAGS = ["-c", "user.name=test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull]


def source(url="https://en.wikipedia.org/wiki/Test"):
    return {"label": "Test source", "url": url, "readOn": ISO(TODAY)}


def event_base(event_id, sport, entries):
    return {
        "id": event_id, "name": "Test " + event_id, "shortName": "Test", "sport": sport,
        "ageCategory": "SENIOR", "level": "TOUR", "officialTierName": "Test tier",
        "location": "Nowhere", "medals": False, "timeZone": "Asia/Shanghai",
        "start": ISO(TODAY - timedelta(days=10)), "end": ISO(TODAY - timedelta(days=5)),
        "status": "FINISHED", "coverage": "Test coverage", "entries": entries, "sources": [source()],
    }


def match(rnd, outcome, scores, opponent=("Opp Player",), country="CHN"):
    return {"round": rnd, "outcome": outcome, "opponent": list(opponent), "opponentCountry": country,
            "scores": scores, "date": None, "time": None}


def entry(discipline, matches, athletes=("Test Player",)):
    return {"country": "IND", "discipline": discipline, "athletes": list(athletes), "seed": None,
            "matches": matches}


def results_doc(updated_on=TODAY):
    badminton = event_base("bwf-results:test-open-2026", "BADMINTON", [
        entry("MS", [match("R16", "WIN", [[21, 15], [21, 18]]), match("QF", "LOSS", [[18, 21], [19, 21]])]),
    ])
    table_tennis = event_base("wtt-results:test-contender-2026", "TABLE_TENNIS", [
        entry("WS", [match("R32", "WIN", [[11, 5], [11, 7], [11, 9]])]),
        {"country": "IND", "discipline": "MT", "athletes": [], "seed": None, "matches": [
            {"round": "R16", "outcome": "WIN", "opponent": [], "opponentCountry": "CHN",
             "scores": [[1, 0]], "date": None, "time": None,
             "rubbers": [{"order": 1, "discipline": "MS", "athletes": ["A One"], "opponent": ["B Two"],
                          "outcome": "WIN", "scores": [[11, 5], [11, 7], [11, 9]]}]},
        ]},
    ])
    return {"schemaVersion": 1, "updatedOn": ISO(updated_on), "sport": "BADMINTON",
            "events": [badminton, table_tennis]}


def upcoming_doc(updated_on=TODAY):
    ev = event_base("bwf-upcoming:test-open-2027", "BADMINTON", [])
    for k in ("status", "coverage", "medals", "timeZone"):  # results-only fields
        del ev[k]
    ev.update({"start": ISO(TODAY + timedelta(days=10)), "end": ISO(TODAY + timedelta(days=15)),
               "entriesPublished": False})
    return {"schemaVersion": 1, "updatedOn": ISO(updated_on), "sport": "BADMINTON", "events": [ev]}


class Workspace:
    """A temp directory holding validate.py, the real rules.json and a pair of data files."""

    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp(prefix="scoreorbit-test-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copy(REPO / "validate.py", self.dir)
        shutil.copy(REPO / "rules.json", self.dir)
        self.results = results_doc()
        self.upcoming = upcoming_doc()
        self.write()

    def write(self):
        (self.dir / "results.json").write_text(json.dumps(self.results, indent=1) + "\n", encoding="utf-8")
        (self.dir / "upcoming.json").write_text(json.dumps(self.upcoming, indent=1) + "\n", encoding="utf-8")

    def run(self, *args):
        self.write()
        done = subprocess.run([sys.executable, "validate.py", *args], cwd=self.dir, capture_output=True,
                              text=True, env=GIT_ENV)
        return done.returncode, done.stdout + done.stderr

    def git(self, *args):
        done = subprocess.run([GIT, *GIT_FLAGS, *args], cwd=self.dir, capture_output=True, text=True, env=GIT_ENV)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def commit(self, message="data"):
        """Commit the data files as they are now (rewrites them first)."""
        self.write()
        if not (self.dir / ".git").exists():
            self.git("init", "-q")
        self.git("add", "results.json", "upcoming.json")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    # handy accessors into the fixture
    def badminton_matches(self):
        return self.results["events"][0]["entries"][0]["matches"]

    def tt_matches(self):
        return self.results["events"][1]["entries"][0]["matches"]


class Base(unittest.TestCase):
    def ws(self):
        return Workspace(self)

    def assertOk(self, result):
        code, out = result
        self.assertEqual(code, 0, out)
        self.assertIn("OK:", out)

    def assertFails(self, result, *needles):
        code, out = result
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        for needle in needles:
            self.assertIn(needle, out)


class FixtureTest(Base):
    def test_unbroken_fixture_passes(self):
        self.assertOk(self.ws().run())


class GameScoreLawTest(Base):
    def run_badminton(self, scores, outcome="WIN"):
        w = self.ws()
        del w.badminton_matches()[1:]  # one match, so a retirement is the entry's last match
        w.badminton_matches()[0].update(scores=scores, outcome=outcome)
        return w.run()

    def run_tt(self, scores, outcome="WIN"):
        w = self.ws()
        w.tt_matches()[0].update(scores=scores, outcome=outcome)
        return w.run()

    def test_badminton_30_2_is_rejected(self):
        self.assertFails(self.run_badminton([[30, 2], [21, 10]]), "game 1 score 30-2 is not a legal game")

    def test_badminton_cap_30_29_is_accepted(self):
        self.assertOk(self.run_badminton([[30, 29], [21, 19]]))

    def test_badminton_21_20_and_20_18_are_rejected(self):
        self.assertFails(self.run_badminton([[21, 20], [21, 10]]), "21-20")
        self.assertFails(self.run_badminton([[20, 18], [21, 10]]), "20-18")

    def test_badminton_past_cap_and_past_21_gap_are_rejected(self):
        self.assertFails(self.run_badminton([[31, 29], [21, 10]]), "above the cap")
        self.assertFails(self.run_badminton([[25, 22], [21, 10]]), "25-22")

    def test_table_tennis_win_by_two_without_cap(self):
        self.assertOk(self.run_tt([[14, 12], [11, 9], [11, 0]]))
        self.assertOk(self.run_tt([[30, 28], [11, 9], [11, 0]]))
        self.assertFails(self.run_tt([[11, 10], [11, 9], [11, 0]]), "11-10")
        self.assertFails(self.run_tt([[30, 29], [11, 9], [11, 0]]), "30-29")

    def test_retired_match_skips_only_its_unfinished_last_game(self):
        # first game complete, retirement in the second game at 4-13: legal
        self.assertOk(self.run_badminton([[11, 21], [4, 13]], "RETIRED_LOSS"))
        self.assertOk(self.run_badminton([[8, 11]], "RETIRED_LOSS"))
        # but an earlier game must still be a legal result
        self.assertFails(self.run_badminton([[20, 15], [4, 13]], "RETIRED_LOSS"), "game 1 score 20-15")

    def test_walkover_has_no_games_to_check(self):
        self.assertOk(self.run_badminton([], "WALKOVER_WIN"))

    def test_rubber_games_follow_the_law_too(self):
        w = self.ws()
        w.results["events"][1]["entries"][1]["matches"][0]["rubbers"][0]["scores"] = [[11, 10], [11, 7], [11, 9]]
        self.assertFails(w.run(), "rubbers[0]", "11-10")

    def test_outcome_must_match_the_games_won(self):
        self.assertFails(self.run_badminton([[15, 21], [18, 21]], "WIN"), "outcome=WIN but games won")
        self.assertFails(self.run_badminton([[21, 15], [21, 18]], "LOSS"), "outcome=LOSS but opponent")


class DateTest(Base):
    def test_future_read_on_is_rejected(self):
        w = self.ws()
        w.results["events"][0]["sources"][0]["readOn"] = ISO(TODAY + timedelta(days=30))
        self.assertFails(w.run(), "readOn", "in the future")

    def test_future_updated_on_is_rejected(self):
        w = self.ws()
        w.upcoming["updatedOn"] = "2999-01-01"
        self.assertFails(w.run(), "upcoming.json: updatedOn = 2999-01-01 is in the future")

    def test_one_day_ahead_is_allowed_for_time_zones_but_two_is_not(self):
        w = self.ws()
        w.results["updatedOn"] = ISO(TODAY + timedelta(days=1))
        w.results["events"][0]["sources"][0]["readOn"] = ISO(TODAY + timedelta(days=1))
        self.assertOk(w.run())
        w.results["updatedOn"] = ISO(TODAY + timedelta(days=2))
        self.assertFails(w.run(), "updatedOn", "in the future")


# validate.py run as if the machine had no time zone database: available_timezones() is empty.
NO_TZ_DATABASE = ("import zoneinfo; zoneinfo.available_timezones = lambda: set(); "
                  "import validate; validate.main([])")


class MedalsAndTimeZoneTest(Base):
    """Every results event has `medals` (a real boolean, never true on a TOUR or DEVELOPMENT event) and a real `timeZone`."""

    def event(self, w):
        return w.results["events"][0]

    def two_parts(self, w, first_medals, second_medals):
        """Make the badminton event two MAJOR parts of one tournament (ids avoid 'team'), with the given medals."""
        first = self.event(w)
        first.update(id="bwf-results:test-games-2026-singles", level="MAJOR", medals=first_medals,
                     tournament={"id": "test-games-2026", "name": "Test Games 2026", "shortName": "Test Games"})
        second = copy.deepcopy(first)
        second.update(id="bwf-results:test-games-2026-doubles", medals=second_medals)
        w.results["events"].insert(1, second)

    def test_medals_is_required(self):
        w = self.ws()
        del self.event(w)["medals"]
        self.assertFails(w.run(), "results.json", "missing 'medals'")

    def test_medals_must_be_a_real_boolean(self):
        w = self.ws()
        for bad in ("true", "false", 1, 0, None):
            self.event(w)["medals"] = bad
            self.assertFails(w.run(), "medals must be true or false", repr(bad))

    def test_medals_true_is_refused_on_tour_and_development_events(self):
        w = self.ws()
        self.event(w)["medals"] = True  # the fixture event is level TOUR
        self.assertFails(w.run(), "medals is true but level is 'TOUR'", "never a TOUR or DEVELOPMENT event")
        self.event(w)["level"] = "DEVELOPMENT"
        self.assertFails(w.run(), "medals is true but level is 'DEVELOPMENT'")
        self.event(w)["level"] = "MAJOR"
        self.assertOk(w.run())

    def test_a_junior_championship_with_no_level_may_award_medals(self):
        w = self.ws()
        self.event(w).update(level=None, ageCategory="JUNIOR", medals=True)  # e.g. World Juniors
        self.assertOk(w.run())

    def test_a_major_event_may_award_no_medals(self):
        w = self.ws()
        self.event(w).update(level="MAJOR", medals=False)  # e.g. a WTT Grand Smash or the World Tour Finals
        self.assertOk(w.run())

    def test_time_zone_is_required(self):
        w = self.ws()
        del self.event(w)["timeZone"]
        self.assertFails(w.run(), "results.json", "missing 'timeZone'")

    def test_time_zone_must_be_a_non_empty_string(self):
        w = self.ws()
        for bad in ("", "  ", None, 5, ["Asia/Tokyo"]):
            self.event(w)["timeZone"] = bad
            self.assertFails(w.run(), "timeZone", "non-empty string")

    @unittest.skipUnless(HAS_TZ_DB, "needs a time zone database")
    def test_time_zone_must_be_a_real_iana_zone(self):
        w = self.ws()
        for bad in ("Mars/Olympus", "asia/shanghai", " Asia/Shanghai", "Shanghai", "UTC+8"):
            self.event(w)["timeZone"] = bad
            self.assertFails(w.run(), f"timeZone {bad!r} is not an IANA time zone name")
        for good in ("Asia/Shanghai", "Asia/Ho_Chi_Minh", "Asia/Kolkata", "Europe/Sofia", "America/Argentina/Buenos_Aires"):
            self.event(w)["timeZone"] = good
            self.assertOk(w.run())

    def test_time_zone_is_not_needed_on_upcoming_events(self):
        w = self.ws()
        self.assertNotIn("timeZone", w.upcoming["events"][0])
        self.assertNotIn("medals", w.upcoming["events"][0])
        self.assertOk(w.run())

    def test_parts_of_one_tournament_must_agree_on_medals(self):
        w = self.ws()
        self.two_parts(w, True, True)
        self.assertOk(w.run())
        del w.results["events"][1]  # drop the second part, then make the parts disagree
        self.two_parts(w, True, False)
        self.assertFails(w.run(), "disagrees with another BADMINTON part on medals",
                         "'bwf-results:test-games-2026-doubles' has medals=false",
                         "'bwf-results:test-games-2026-singles' has medals=true")

    def test_parts_of_one_tournament_may_agree_on_no_medals(self):
        w = self.ws()
        self.two_parts(w, False, False)
        self.assertOk(w.run())

    def test_parts_of_different_sports_do_not_have_to_agree(self):
        w = self.ws()
        self.two_parts(w, True, True)
        w.results["events"][1]["sport"] = "TABLE_TENNIS"
        w.results["events"][1]["id"] = "wtt-results:test-games-2026-doubles"
        w.results["events"][1].update(medals=False, level="MAJOR")
        w.results["events"][1]["entries"] = [entry("WS", [match("R32", "WIN", [[11, 5], [11, 7], [11, 9]])])]
        self.assertOk(w.run())

    def test_shape_only_check_without_a_time_zone_database(self):
        with mock.patch.object(validate, "known_time_zones", return_value=frozenset()):
            for good in ("Asia/Shanghai", "America/Argentina/Buenos_Aires", "America/Port-au-Prince", "Etc/GMT+5",
                          "Mars/Olympus"):
                self.assertEqual(validate.time_zone_problem(good), (None, True), good)
            for bad in ("", "  ", "Shanghai", "Asia/", "/Shanghai", "Asia//Tokyo", "Asia/Tok yo", "12/30", None):
                problem, shape_only = validate.time_zone_problem(bad)
                self.assertTrue(problem, bad)
                self.assertFalse(shape_only, bad)

    @unittest.skipUnless(HAS_TZ_DB, "needs a time zone database")
    def test_a_machine_with_a_time_zone_database_prints_no_note(self):
        code, out = self.ws().run()
        self.assertEqual(code, 0, out)
        self.assertNotIn("time zone database", out)

    def test_no_time_zone_database_means_a_note_not_a_failure(self):
        w = self.ws()
        self.event(w)["timeZone"] = "Mars/Olympus"  # well formed, so only its shape can be checked
        w.write()
        done = subprocess.run([sys.executable, "-c", NO_TZ_DATABASE], cwd=w.dir, capture_output=True, text=True,
                              env=GIT_ENV)
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("OK:", out)
        notes = [line for line in out.splitlines() if "no time zone database" in line]
        self.assertEqual(len(notes), 1, out)
        self.assertTrue(notes[0].startswith("note: results.json:"), notes[0])
        self.assertTrue(all("OK" not in line for line in out.splitlines() if line.startswith("note:")), out)
        # a malformed name still fails there
        self.event(w)["timeZone"] = "Shanghai"
        w.write()
        done = subprocess.run([sys.executable, "-c", NO_TZ_DATABASE], cwd=w.dir, capture_output=True, text=True,
                              env=GIT_ENV)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("not a well-formed IANA time zone name", done.stdout)


class SourceHostTest(Base):
    def run_url(self, url):
        w = self.ws()
        w.results["events"][0]["sources"][0]["url"] = url
        return w.run()

    def test_allowed_hosts_and_their_subdomains(self):
        for url in ("https://en.wikipedia.org/wiki/X", "https://wikipedia.org/wiki/X",
                    "https://bwfworldtour.bwfbadminton.com/results", "https://www.tribuneindia.com/a",
                    "https://thebridge.in/a", "https://worldtabletennis.com/b"):
            self.assertOk(self.run_url(url))

    def test_unlisted_hosts_are_rejected(self):
        for url in ("https://example.com/x", "https://en.wikipedia.org.evil.com/x",
                    "https://evilwikipedia.org/x", "https://en.wikipedia.org@evil.com/x"):
            self.assertFails(self.run_url(url), "not in ALLOWED_SOURCE_HOSTS")

    def test_non_http_urls_are_rejected(self):
        self.assertFails(self.run_url("ftp://en.wikipedia.org/x"), "not an http(s) URL")
        self.assertFails(self.run_url("en.wikipedia.org/wiki/X"), "not an http(s) URL")


class ProgressionTest(Base):
    def run_matches(self, matches, discipline="MS"):
        w = self.ws()
        w.results["events"][0]["entries"][0].update(discipline=discipline, matches=matches)
        return w.run()

    win = [[21, 10], [21, 10]]
    loss = [[10, 21], [10, 21]]

    def test_match_after_a_knockout_loss_is_rejected(self):
        self.assertFails(self.run_matches([match("R16", "LOSS", self.loss), match("QF", "WIN", self.win)]),
                         "comes after a knockout loss")

    def test_walkover_and_retired_losses_count_as_knockout_losses(self):
        self.assertFails(self.run_matches([match("R16", "WALKOVER_LOSS", []), match("QF", "WIN", self.win)]),
                         "comes after a knockout loss")
        self.assertFails(self.run_matches([match("R16", "RETIRED_LOSS", [[8, 11]]), match("QF", "WIN", self.win)]),
                         "comes after a knockout loss")

    def test_scheduled_matches_after_a_loss_are_not_decided(self):
        w = self.ws()
        w.results["events"][0]["status"] = "IN_PROGRESS"  # a FINISHED event may not hold SCHEDULED matches
        w.results["events"][0]["entries"][0]["matches"] = [match("R16", "LOSS", self.loss),
                                                           match("QF", "SCHEDULED", [])]
        self.assertOk(w.run())

    def test_group_loss_then_knockout_is_fine(self):
        self.assertOk(self.run_matches([match("G1", "LOSS", self.loss), match("G2", "WIN", self.win),
                                        match("R16", "WIN", self.win)]))

    def test_semi_final_loser_may_play_the_third_place_match(self):
        self.assertOk(self.run_matches([match("SF", "LOSS", self.loss), match("3P", "WIN", self.win)]))

    def test_lucky_loser_enters_the_main_draw_after_a_qualifying_loss(self):
        self.assertOk(self.run_matches([match("Q1", "WIN", self.win), match("Q2", "LOSS", self.loss),
                                        match("R64", "WIN", self.win)]))
        self.assertFails(self.run_matches([match("Q1", "LOSS", self.loss), match("Q2", "WIN", self.win)]),
                         "comes after a knockout loss")

    def test_team_ties_follow_the_same_rule(self):
        w = self.ws()
        ties = w.results["events"][1]["entries"][1]["matches"]
        ties[0].update(outcome="LOSS", scores=[[0, 1]], rubbers=[])
        ties.append({"round": "QF", "outcome": "WIN", "opponent": [], "opponentCountry": "KOR",
                     "scores": [[1, 0]], "date": None, "time": None})
        self.assertFails(w.run(), "comes after a knockout loss")


class AppParserRulesTest(Base):
    """A few of the rules mirrored from the app's parsers (step 1); the full list is in validate.py."""

    def test_null_required_string_is_rejected(self):
        w = self.ws()
        w.upcoming["events"][0]["location"] = None
        self.assertFails(w.run(), "'location' is null")

    def test_entries_published_must_be_a_boolean(self):
        w = self.ws()
        w.upcoming["events"][0]["entriesPublished"] = "true"
        self.assertFails(w.run(), "entriesPublished must be true or false")

    def test_source_label_is_required(self):
        w = self.ws()
        del w.results["events"][0]["sources"][0]["label"]
        self.assertFails(w.run(), "missing 'label'")

    def test_match_time_must_be_iso(self):
        w = self.ws()
        w.badminton_matches()[0]["time"] = "10:30"
        self.assertOk(w.run())
        for bad in ("25:00", "9:30", "10:30pm"):
            w.badminton_matches()[0]["time"] = bad
            self.assertFails(w.run(), "not an ISO local time")

    def test_scores_must_be_a_list(self):
        w = self.ws()
        w.badminton_matches()[0]["scores"] = None
        self.assertFails(w.run(), "scores must be a list")

    def test_rubbers_only_on_ties_and_must_be_a_list(self):
        w = self.ws()
        w.badminton_matches()[0]["rubbers"] = [{"order": 1}]
        self.assertFails(w.run(), "rubbers only belong on a team tie")
        w.badminton_matches()[0]["rubbers"] = {}
        self.assertFails(w.run(), "rubbers must be a list")

    def test_tie_cannot_be_not_played(self):
        w = self.ws()
        w.results["events"][1]["entries"][1]["matches"][0].update(outcome="NOT_PLAYED", scores=[])
        self.assertFails(w.run(), "a tie can't be NOT_PLAYED")

    def test_nan_is_rejected(self):
        w = self.ws()
        w.write()
        path = w.dir / "results.json"
        path.write_text(path.read_text().replace('"seed": null', '"seed": NaN', 1), encoding="utf-8")
        done = subprocess.run([sys.executable, "validate.py"], cwd=w.dir, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("contains NaN", done.stdout)

    def test_rules_json_may_not_go_beyond_the_app_enums(self):
        w = self.ws()
        rules = json.loads((w.dir / "rules.json").read_text())
        rules["BADMINTON"]["outcomes"].append("FORFEIT")
        (w.dir / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
        self.assertFails(w.run(), "are not values the app knows")


def person(name="Tanvi Sharma", aliases=("T. Sharma", "T SHARMA"), sport="BADMINTON", country="IND"):
    return {"sport": sport, "country": country, "name": name, "aliases": list(aliases)}


class NamesDirectoryTest(Base):
    """results.json's optional `names`: one person per entry, one spelling per person in a sport and country."""

    def run_names(self, *people):
        w = self.ws()
        w.results["names"] = list(people)
        return w.run()

    def run_names_raw(self, value):
        w = self.ws()
        w.results["names"] = value
        return w.run()

    def test_no_names_section_is_fine(self):
        w = self.ws()
        self.assertNotIn("names", w.results)
        self.assertOk(w.run())

    def test_the_documented_example_passes(self):
        # Aliases that the app normalises to one key ("T. Sharma", "T SHARMA") may both be listed.
        self.assertOk(self.run_names(person(), person("P. V. Sindhu", ["Pusarla V. Sindhu"])))

    def test_names_must_be_a_list_of_objects(self):
        for bad in ({}, "Tanvi Sharma", None):
            self.assertFails(self.run_names_raw(bad), "names must be a list of people")
        self.assertFails(self.run_names("Tanvi Sharma"), "names[0] must be an object")

    def test_sport_must_be_supported(self):
        self.assertFails(self.run_names(person(sport="SQUASH")), "names[0] sport 'SQUASH' is not a supported sport")
        self.assertFails(self.run_names({k: v for k, v in person().items() if k != "sport"}), "sport None")
        self.assertOk(self.run_names(person(sport="TABLE_TENNIS")))

    def test_country_must_be_an_ioc_code(self):
        self.assertFails(self.run_names(person(country="IRN")), "names[0] country 'IRN' is not an IOC code")
        self.assertFails(self.run_names(person(country="ind")), "must be exactly 3 uppercase letters")
        self.assertFails(self.run_names(person(country=None)), "names[0] country is null")

    def test_name_must_be_a_non_empty_string(self):
        for bad in ("", "   ", ". .", None, 5, ["Tanvi Sharma"]):
            self.assertFails(self.run_names(person(name=bad)), "names[0] name must be a non-empty string")

    def test_aliases_must_be_a_non_empty_list_of_non_empty_strings(self):
        for bad in ([], None, "T. Sharma"):
            w = self.ws()
            w.results["names"] = [dict(person(), aliases=bad)]
            self.assertFails(w.run(), "aliases must be a non-empty list of other spellings")
        for bad in ("", "  ", None, 7):
            self.assertFails(self.run_names(person(aliases=["T. Sharma", bad])), f"alias {bad!r} must be a non-empty string")

    def test_aliases_are_distinct_and_never_the_name(self):
        self.assertFails(self.run_names(person(aliases=["T. Sharma", "T. Sharma"])), "alias 'T. Sharma' is listed twice")
        self.assertFails(self.run_names(person(aliases=["Tanvi Sharma"])), "alias 'Tanvi Sharma' is the person's name")

    def test_one_alias_for_two_people_is_rejected(self):
        self.assertFails(self.run_names(person(), person("Tara Sharma", ["T. Sharma"])),
                         "names[1] 'Tara Sharma' alias 'T. Sharma' is also the alias 'T. Sharma' of names[0]",
                         "one spelling can name only one person")
        # compared the way the app compares names: "T SHARMA" and "t. sharma" are the same key
        self.assertFails(self.run_names(person(aliases=["T SHARMA"]), person("Tara Sharma", ["t. sharma"])),
                         "alias 't. sharma' is also the alias 'T SHARMA' of names[0]")

    def test_an_alias_may_not_be_another_persons_name(self):
        self.assertFails(self.run_names(person(), person("Tara Sharma", ["Tanvi Sharma"])),
                         "alias 'Tanvi Sharma' is also the name 'Tanvi Sharma' of names[0]")
        self.assertFails(self.run_names(person("Tara Sharma", ["Tanvi Sharma"]), person()),
                         "name 'Tanvi Sharma' is also the alias 'Tanvi Sharma' of names[0]")

    def test_one_person_one_entry(self):
        self.assertFails(self.run_names(person(), person(aliases=["Sharma Tanvi"])),
                         "names[1] 'Tanvi Sharma' is a second entry for BADMINTON IND 'Tanvi Sharma' (names[0])")
        self.assertFails(self.run_names(person(), person("TANVI SHARMA", ["Sharma Tanvi"])), "is a second entry for")

    def test_the_same_spelling_in_another_sport_or_country_is_another_person(self):
        self.assertOk(self.run_names(person(), person(country="SGP"), person(sport="TABLE_TENNIS")))
        self.assertOk(self.run_names(person(), person("Tara Sharma", ["T. Sharma"], country="SGP")))

    def test_an_em_dash_in_a_name_is_rejected(self):
        w = self.ws()
        w.results["names"] = [person(aliases=["T. Sharma DASH IND"])]
        w.write()  # json.dumps escapes non-ASCII, so put the dash itself in the file
        path = w.dir / "results.json"
        path.write_text(path.read_text().replace("DASH", "\u2014"), encoding="utf-8")
        done = subprocess.run([sys.executable, "validate.py"], cwd=w.dir, capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("results.json: contains an em dash", done.stdout)

    def test_an_escaped_em_dash_is_rejected_too(self):
        w = self.ws()
        w.results["names"] = [person(aliases=["T. Sharma \u2014 IND"])]
        w.write()  # json.dumps writes the dash as the escape \u2014, which the app decodes to a dash
        done = subprocess.run([sys.executable, "validate.py"], cwd=w.dir, capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("results.json: contains an escaped em dash", done.stdout)

    def test_names_belong_in_results_json_only(self):
        w = self.ws()
        w.upcoming["names"] = [person()]
        self.assertFails(w.run(), "upcoming.json: 'names' belongs in results.json")

    def test_name_key_matches_the_apps_normalisation(self):
        self.assertEqual(validate.name_key("P.V. Sindhu"), "p v sindhu")
        self.assertEqual(validate.name_key("  T  SHARMA "), "t sharma")
        self.assertEqual(validate.name_key("Chi Yu-jen"), "chi yu jen")
        self.assertEqual(validate.name_key("Lê Đức Phát"), "le uc phat")


@unittest.skipUnless(GIT, "git is needed for the previous-version checks")
class PreviousVersionTest(Base):
    def test_working_tree_is_compared_with_head(self):
        w = self.ws()
        w.commit()
        w.results["updatedOn"] = ISO(TODAY - timedelta(days=3))
        self.assertFails(w.run(), "results.json: updatedOn", "earlier than", "HEAD (working tree")

    def test_updated_on_may_be_kept_or_raised(self):
        w = self.ws()
        w.results["updatedOn"] = ISO(TODAY - timedelta(days=1))
        w.upcoming["updatedOn"] = ISO(TODAY - timedelta(days=1))
        w.commit()
        w.badminton_matches()[0]["round"] = "R32"  # a correction that keeps the date
        self.assertOk(w.run())
        w.results["updatedOn"] = ISO(TODAY)  # and a later one that raises it
        self.assertOk(w.run())

    def test_dropping_a_results_event_is_rejected(self):
        w = self.ws()
        w.commit()
        dropped = w.results["events"].pop()["id"]
        self.assertFails(w.run(), f"event {dropped!r}", "missing now")

    def test_dropping_an_upcoming_event_is_fine(self):
        w = self.ws()
        w.commit()
        w.upcoming["events"].clear()
        self.assertOk(w.run())

    def test_clean_tree_is_compared_with_head_parent(self):
        """CI: the commit under test is HEAD and the working tree matches it."""
        w = self.ws()
        w.commit("first")
        dropped = w.results["events"].pop()["id"]
        w.commit("second drops an event")
        self.assertFails(w.run(), f"event {dropped!r}", "HEAD~1")
        w.results["events"].append(results_doc()["events"][1])
        w.commit("third restores it")
        self.assertOk(w.run())

    def test_clean_tree_updated_on_going_backwards_is_rejected(self):
        w = self.ws()
        w.commit("first")
        w.upcoming["updatedOn"] = ISO(TODAY - timedelta(days=2))
        w.commit("second lowers updatedOn")
        self.assertFails(w.run(), "upcoming.json: updatedOn", "earlier than", "HEAD~1")

    def test_single_commit_has_nothing_to_compare_and_says_so(self):
        w = self.ws()
        w.commit()
        code, out = w.run()
        self.assertEqual(code, 0, out)
        self.assertIn("note: results.json: previous-version checks skipped", out)
        self.assertIn("OK:", out)

    def test_not_a_git_repository_skips_with_a_note(self):
        code, out = self.ws().run()
        self.assertEqual(code, 0, out)
        self.assertIn("previous-version checks skipped", out)

    def test_previous_ref_overrides_the_head_rule(self):
        w = self.ws()
        base = w.commit("main")
        gone = w.results["events"].pop()["id"]
        w.commit("a commit that lost an event")
        w.commit("next commit, no change")
        # HEAD~1 already lacks the event, so the plain run sees nothing wrong ...
        self.assertOk(w.run())
        # ... but comparing against the ref that still had it does.
        self.assertFails(w.run("--previous-ref", base), f"event {gone!r}", base)
        self.assertFails(w.run(f"--previous-ref={base[:10]}"), f"event {gone!r}")

    def test_previous_ref_checks_updated_on_against_that_ref(self):
        w = self.ws()
        base = w.commit("main")
        w.results["updatedOn"] = ISO(TODAY - timedelta(days=4))
        w.commit("lowered")
        self.assertFails(w.run("--previous-ref", base), "earlier than", base)

    def test_previous_ref_without_the_file_is_skipped_with_a_note(self):
        w = self.ws()
        w.git("init", "-q")
        w.write()
        w.git("add", "results.json")
        w.git("commit", "-q", "-m", "only results")
        first = w.git("rev-parse", "HEAD")
        w.commit("adds upcoming")
        code, out = w.run("--previous-ref", first)
        self.assertEqual(code, 0, out)
        self.assertIn(f"note: upcoming.json: previous-version checks skipped ({first} has no upcoming.json)", out)

    def test_unknown_previous_ref_fails_instead_of_skipping(self):
        w = self.ws()
        w.commit()
        self.assertFails(w.run("--previous-ref", "no-such-ref"), "no-such-ref", "does not resolve")
        self.assertFails(w.run("--previous-ref=--output=x"), "--output=x")


def quiet_check(today, base):
    with contextlib.redirect_stdout(io.StringIO()):
        return heartbeat.check(today, base)


class HeartbeatTest(unittest.TestCase):
    def make(self, results_on, upcoming_on):
        d = Path(tempfile.mkdtemp(prefix="scoreorbit-heartbeat-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "results.json").write_text(json.dumps({"updatedOn": results_on}))
        (d / "upcoming.json").write_text(json.dumps({"updatedOn": upcoming_on}))
        return str(d)

    def test_two_days_old_passes_three_days_old_fails(self):
        today = date(2026, 10, 8)
        base = self.make("2026-10-06", "2026-10-01")
        self.assertEqual(quiet_check(today, base), [])
        base = self.make("2026-10-05", "2026-10-04")
        problems = quiet_check(today, base)
        self.assertEqual(len(problems), 1)
        self.assertIn("results.json", problems[0])
        self.assertIn("3 days", problems[0])

    def test_a_week_old_upcoming_file_is_fine_when_results_is_fresh(self):
        # upcoming.json changes about weekly; results.json is bumped on every run.
        self.assertEqual(quiet_check(date(2026, 10, 8), self.make("2026-10-08", "2026-09-28")), [])

    def test_unreadable_or_missing_updated_on_fails(self):
        base = self.make("not a date", "2026-10-08")
        problems = quiet_check(date(2026, 10, 8), base)
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot read updatedOn", problems[0])
        Path(base, "upcoming.json").unlink()
        self.assertEqual(len(quiet_check(date(2026, 10, 8), base)), 2)


if __name__ == "__main__":
    unittest.main()
