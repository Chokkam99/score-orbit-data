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


class SquashTest(Base):
    """Squash shares table tennis's game law (11, win by 2, no cap) but has no third-place match."""

    def squash_event(self, matches, **extra):
        ev = event_base("squash-results:test-open-2026", "SQUASH", [entry("MS", matches)])
        ev.update(extra)
        return ev

    def run_with(self, event):
        w = self.ws()
        w.results["events"].append(event)
        return w.run()

    def test_games_to_eleven(self):
        self.assertOk(self.run_with(self.squash_event([match("R32", "WIN", [[11, 7], [12, 14], [11, 9], [11, 4]])])))
        self.assertFails(self.run_with(self.squash_event([match("R32", "WIN", [[11, 10], [11, 7], [11, 9]])])), "11-10")

    def test_no_third_place_round(self):
        self.assertFails(self.run_with(self.squash_event([match("3P", "WIN", [[11, 7], [11, 9], [11, 4]])])),
                         "round '3P' not in allowed set")

    def test_event_game_law_overrides_the_sport(self):
        # The Squash World Cup: games to 7, sudden death at 6-6.
        seven = {"pointsToWin": 7, "winBy": 1, "cap": None}
        games = [[7, 6], [7, 3], [7, 5]]
        self.assertFails(self.run_with(self.squash_event([match("R32", "WIN", games)])), "7-6")
        self.assertOk(self.run_with(self.squash_event([match("R32", "WIN", games)], gameScoring=seven)))

    def test_a_broken_event_game_law_is_reported(self):
        bad = {"pointsToWin": 0, "winBy": 1, "cap": None}
        self.assertFails(self.run_with(self.squash_event([match("R32", "WIN", [[11, 7], [11, 9], [11, 4]])],
                                                         gameScoring=bad)),
                         "gameScoring pointsToWin must be an integer >= 1")


class CountryCodeTest(Base):
    def test_morocco_kosovo_macau_neutral_and_refugee_codes_are_accepted(self):
        for code in ("MAR", "KOS", "AIN", "EOR", "MAC"):
            w = self.ws()
            w.badminton_matches()[0]["opponentCountry"] = code
            self.assertOk(w.run())

    def test_non_ioc_codes_are_still_rejected(self):
        w = self.ws()
        w.badminton_matches()[0]["opponentCountry"] = "IRN"
        self.assertFails(w.run(), "'IRN' is not an IOC code")


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
        self.assertFails(self.run_names(person(sport="BASKETBALL")), "names[0] sport 'BASKETBALL' is not a supported sport")
        self.assertOk(self.run_names(person(sport="SQUASH")))
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


# ---------------------------------------------------------------------------------------------------
# Score sports: boxing, wrestling, archery, hockey, kabaddi (rules.json `scoreFormat: "score"`).
#
# The law tests below call validate.validate_file in this process (the code `python3 validate.py` runs,
# without a subprocess per case, so a table of cases stays fast). Tests that read the real rules.json
# and the real examples from the probes go through `python3 validate.py` like the tests above.
# ---------------------------------------------------------------------------------------------------

RULES = json.loads((REPO / "rules.json").read_text(encoding="utf-8"))

# Disciplines as an entry writes them (the three fields repeat rules.json) and a bare racket code.
BOX_M60 = {"discipline": "M60", "disciplineName": "Men's 60 kg", "gender": "M", "kind": "SINGLE"}
BOX_W57 = {"discipline": "W57", "disciplineName": "Women's 57 kg", "gender": "W", "kind": "SINGLE"}
BOX_MPLUS90 = {"discipline": "M+90", "disciplineName": "Men's +90 kg", "gender": "M", "kind": "SINGLE"}
BOX_WPLUS80 = {"discipline": "W+80", "disciplineName": "Women's +80 kg", "gender": "W", "kind": "SINGLE"}
WRE_FS57 = {"discipline": "FS57", "disciplineName": "Men's freestyle 57 kg", "gender": "M", "kind": "SINGLE"}
WRE_GR60 = {"discipline": "GR60", "disciplineName": "Men's Greco-Roman 60 kg", "gender": "M", "kind": "SINGLE"}
WRE_WW53 = {"discipline": "WW53", "disciplineName": "Women's freestyle 53 kg", "gender": "W", "kind": "SINGLE"}
ARC_RM = {"discipline": "RM", "disciplineName": "Recurve men", "gender": "M", "kind": "SINGLE"}
ARC_RW = {"discipline": "RW", "disciplineName": "Recurve women", "gender": "W", "kind": "SINGLE"}
ARC_CM = {"discipline": "CM", "disciplineName": "Compound men", "gender": "M", "kind": "SINGLE"}
ARC_CW = {"discipline": "CW", "disciplineName": "Compound women", "gender": "W", "kind": "SINGLE"}
ARC_RMT = {"discipline": "RMT", "disciplineName": "Recurve men's team", "gender": "M", "kind": "CREW"}
ARC_RXT = {"discipline": "RXT", "disciplineName": "Recurve mixed team", "gender": "X", "kind": "CREW"}
ARC_CMT = {"discipline": "CMT", "disciplineName": "Compound men's team", "gender": "M", "kind": "CREW"}
ARC_CXT = {"discipline": "CXT", "disciplineName": "Compound mixed team", "gender": "X", "kind": "CREW"}
BARE_MT = {"discipline": "MT"}
BARE_WT = {"discipline": "WT"}

CREW_OF = {"RMT": 3, "RWT": 3, "CMT": 3, "CWT": 3, "RXT": 2, "CXT": 2}


def score_athletes(spec):
    """The athletes the discipline's code lists: none for MT and WT, a crew's size for archery teams, else one."""
    code = spec["discipline"]
    if code in ("MT", "WT"):
        return []
    return [f"Archer {n}" for n in range(1, CREW_OF[code] + 1)] if code in CREW_OF else ["Test Player"]


def score_entry(spec, matches, athletes=None, country="IND"):
    return {"country": country, "athletes": score_athletes(spec) if athletes is None else athletes,
            "seed": None, "matches": matches, **spec}


def score_match(rnd, outcome, scores, country="CHN", opponent=("Opp Player",), **extra):
    return {**match(rnd, outcome, scores, opponent=opponent, country=country), **extra}


def team_match(rnd, outcome, scores, country="CHN", **extra):
    return score_match(rnd, outcome, scores, country=country, opponent=(), **extra)


def score_event(sport, entries, event_id=None, **extra):
    """A results event valid for all five score sports (kabaddi is MAJOR only)."""
    ev = event_base(event_id or f"{sport.lower()}-results:test-2026", sport, entries)
    ev.update(level="MAJOR", **extra)
    return ev


def score_errors(events, rules=None):
    """The problems validate.validate_file finds in these results events."""
    errors = validate.Errors()
    doc = {"schemaVersion": 1, "updatedOn": ISO(TODAY), "sport": events[0]["sport"], "events": events}
    validate.validate_file("results.json", doc, validate.RESULTS_EVENT_REQUIRED, validate.RESULTS_EVENT_NON_NULL,
                           rules or RULES, errors, is_results=True)
    return list(errors)


def rules_errors(rules):
    errors = validate.Errors()
    validate.check_rules_against_app(rules, errors)
    return list(errors)


def edited_rules(edit):
    rules = copy.deepcopy(RULES)
    edit(rules)
    return rules


class ScoreCase(unittest.TestCase):
    """Helpers for the in-process law tests."""

    def errors_for(self, sport, spec, matches, athletes=None, status="FINISHED", rules=None, country="IND"):
        ev = score_event(sport, [score_entry(spec, matches, athletes, country)], status=status)
        return score_errors([ev], rules)

    def boxing(self, matches, spec=BOX_M60, **kw):
        return self.errors_for("BOXING", spec, matches, **kw)

    def wrestling(self, matches, spec=WRE_WW53, **kw):
        return self.errors_for("WRESTLING", spec, matches, **kw)

    def archery(self, matches, spec=ARC_RW, **kw):
        return self.errors_for("ARCHERY", spec, matches, **kw)

    def hockey(self, matches, spec=BARE_MT, **kw):
        return self.errors_for("HOCKEY", spec, matches, **kw)

    def kabaddi(self, matches, spec=BARE_WT, **kw):
        return self.errors_for("KABADDI", spec, matches, **kw)

    def assertClean(self, errors):
        self.assertEqual(errors, [])

    def assertProblem(self, errors, *needles):
        """At least one problem, and the needles all appear in the report."""
        self.assertTrue(errors, "expected a problem, found none")
        joined = "\n".join(errors)
        for needle in needles:
            self.assertIn(needle, joined)

    def assertOneProblem(self, errors, *needles):
        """Exactly one problem (so the case fails for the reason under test only), holding the needles."""
        self.assertEqual(len(errors), 1, "\n".join(errors))
        for needle in needles:
            self.assertIn(needle, errors[0])


class ScoreRulesJsonTest(ScoreCase):
    """The five blocks in the real rules.json."""

    def test_the_five_score_sports_are_in_rules_json(self):
        for sport in ("BOXING", "WRESTLING", "ARCHERY", "HOCKEY", "KABADDI"):
            self.assertEqual(RULES[sport]["scoreFormat"], "score", sport)
            self.assertNotIn("gameScoring", RULES[sport], sport)
            self.assertEqual(validate.score_rules_problems(RULES[sport]), [], sport)
        self.assertEqual(rules_errors(RULES), [])

    def test_boxing_weight_classes(self):
        codes = list(RULES["BOXING"]["disciplines"])
        self.assertEqual(codes, [f"M{w}" for w in (50, 55, 60, 65, 70, 75, 80, 85, 90)] + ["M+90"]
                         + [f"W{w}" for w in (48, 51, 54, 57, 60, 65, 70, 75, 80)] + ["W+80"])
        self.assertEqual(RULES["BOXING"]["disciplines"]["M+90"],
                         {"name": "Men's +90 kg", "gender": "M", "kind": "SINGLE"})

    def test_wrestling_weight_classes(self):
        codes = list(RULES["WRESTLING"]["disciplines"])
        self.assertEqual(codes, [f"FS{w}" for w in (57, 61, 65, 70, 74, 79, 86, 92, 97, 125)]
                         + [f"GR{w}" for w in (55, 60, 63, 67, 72, 77, 82, 87, 97, 130)]
                         + [f"WW{w}" for w in (50, 53, 55, 57, 59, 62, 65, 68, 72, 76)])
        genders = {c: d["gender"] for c, d in RULES["WRESTLING"]["disciplines"].items()}
        self.assertEqual({g for c, g in genders.items() if c[:2] in ("FS", "GR")}, {"M"})
        self.assertEqual({g for c, g in genders.items() if c.startswith("WW")}, {"W"})

    def test_archery_events(self):
        discs = RULES["ARCHERY"]["disciplines"]
        self.assertEqual(list(discs), ["RM", "RW", "CM", "CW", "RMT", "RWT", "CMT", "CWT", "RXT", "CXT"])
        self.assertEqual({c for c, d in discs.items() if d["kind"] == "SINGLE"}, {"RM", "RW", "CM", "CW"})
        self.assertEqual({c: d["athletes"] for c, d in discs.items() if d["kind"] == "CREW"},
                         {"RMT": 3, "RWT": 3, "CMT": 3, "CWT": 3, "RXT": 2, "CXT": 2})
        self.assertEqual({c: d["maxTotal"] for c, d in discs.items() if d["scoring"] == "compound"},
                         {"CM": 150, "CW": 150, "CMT": 240, "CWT": 240, "CXT": 160})

    def test_hockey_and_kabaddi_use_the_bare_team_codes(self):
        for sport in ("HOCKEY", "KABADDI"):
            self.assertEqual(list(RULES[sport]["disciplines"]), ["MT", "WT"])
            self.assertEqual({d["kind"] for d in RULES[sport]["disciplines"].values()}, {"TEAM"})

    def test_draw_is_an_outcome_of_hockey_and_kabaddi_only(self):
        with_draw = {sport for sport, rules in RULES.items() if "DRAW" in rules["outcomes"]}
        self.assertEqual(with_draw, {"HOCKEY", "KABADDI"})

    def test_third_place_matches_follow_the_spec(self):
        self.assertEqual({s: RULES[s]["thirdPlaceMatch"] for s in ("BOXING", "WRESTLING", "ARCHERY", "HOCKEY", "KABADDI")},
                         {"BOXING": False, "WRESTLING": True, "ARCHERY": True, "HOCKEY": True, "KABADDI": False})

    def test_rounds_and_levels_per_sport(self):
        self.assertEqual(RULES["BOXING"]["rounds"], ["R64", "R32", "R16", "QF", "SF", "F"])
        self.assertEqual(RULES["WRESTLING"]["rounds"], ["R32", "R16", "QF", "SF", "F", "REP1", "REP2", "3P"])
        self.assertEqual(RULES["ARCHERY"]["rounds"][:6], ["R128", "R96", "R64", "R48", "R32", "R24"])
        self.assertEqual(RULES["HOCKEY"]["rounds"][:16], [f"G{n}" for n in range(1, 17)])
        self.assertEqual(RULES["HOCKEY"]["rounds"][16:], ["5P", "7P", "9P", "11P", "13P", "15P", "QF", "SF", "3P", "F"])
        self.assertEqual(RULES["KABADDI"]["rounds"], [f"G{n}" for n in range(1, 17)] + ["QF", "SF", "F"])
        self.assertEqual(RULES["KABADDI"]["levels"], ["MAJOR"])
        self.assertEqual(RULES["ARCHERY"]["levels"], ["MAJOR", "TOUR", "DEVELOPMENT"])


class ScoreRulesConfigTest(ScoreCase):
    """A broken score-sport block in rules.json is reported, not trusted."""

    def broken(self, sport, edit):
        return rules_errors(edited_rules(lambda r: edit(r[sport])))

    def test_draw_passes_the_app_enum_mirror_and_an_unknown_outcome_does_not(self):
        self.assertIn("DRAW", validate.APP_ENUMS["outcomes"])
        # DRAW in a racket sport's list is a value the app knows (the data may still never use it there).
        self.assertClean(self.broken("BADMINTON", lambda b: b["outcomes"].append("DRAW")))
        self.assertProblem(self.broken("HOCKEY", lambda b: b["outcomes"].append("FORFEIT")),
                           "HOCKEY outcomes ['FORFEIT'] are not values the app knows")

    def test_a_discipline_needs_a_kind_a_gender_and_a_name(self):
        def edit(b):
            b["disciplines"]["M60"]["kind"] = "SQUAD"
            b["disciplines"]["M65"]["gender"] = "B"
            b["disciplines"]["M70"]["name"] = " "
        errors = self.broken("BOXING", edit)
        self.assertProblem(errors, "discipline 'M60' kind must be one of SINGLE, PAIR, CREW, TEAM",
                           "discipline 'M65' gender must be one of M, W, X", "discipline 'M70' needs a non-empty name")

    def test_disciplines_must_be_an_object_for_a_score_sport(self):
        self.assertProblem(self.broken("BOXING", lambda b: b.update(disciplines=["M60"])),
                           "BOXING disciplines must be an object")

    def test_pinned_athletes_must_fit_the_kind(self):
        self.assertProblem(self.broken("ARCHERY", lambda b: b["disciplines"]["RMT"].update(athletes=7)),
                           "discipline 'RMT' athletes must be a whole number from 2 to 6 for kind CREW")
        self.assertProblem(self.broken("BOXING", lambda b: b["disciplines"]["M60"].update(athletes=2)),
                           "discipline 'M60' athletes must be a whole number from 1 to 1 for kind SINGLE")

    def test_a_racket_code_in_a_score_sport_must_be_a_team(self):
        self.assertProblem(self.broken("HOCKEY", lambda b: b["disciplines"].update(MS={"name": "x", "gender": "M", "kind": "SINGLE"})),
                           "discipline 'MS' is a racket code")

    def test_third_place_match_must_agree_with_the_rounds(self):
        self.assertProblem(self.broken("BOXING", lambda b: b.update(thirdPlaceMatch=True)),
                           "BOXING thirdPlaceMatch is true but 3P is not one of its rounds")
        self.assertProblem(self.broken("WRESTLING", lambda b: b.update(thirdPlaceMatch=False)),
                           "WRESTLING thirdPlaceMatch is false but 3P is one of its rounds")
        self.assertProblem(self.broken("KABADDI", lambda b: b.pop("thirdPlaceMatch")),
                           "KABADDI thirdPlaceMatch must be true or false")

    def test_a_score_sport_has_no_game_scoring(self):
        self.assertProblem(self.broken("HOCKEY", lambda b: b.update(gameScoring={"pointsToWin": 11, "winBy": 2, "cap": None})),
                           "HOCKEY has no gameScoring")

    def test_a_method_must_use_the_sports_outcomes_and_a_known_score_policy(self):
        def edit(b):
            b["methods"]["DEC"]["outcomes"] = ["WIN", "DRAW"]
            b["methods"]["KO"]["scores"] = "sometimes"
        errors = self.broken("BOXING", edit)
        self.assertProblem(errors, "method 'DEC' outcomes must be a non-empty subset of the sport's outcomes",
                           "method 'KO' scores must be one of pair, empty, any")

    def test_a_method_that_records_a_round_needs_the_largest_round(self):
        self.assertProblem(self.broken("BOXING", lambda b: b.pop("methodRoundMax")),
                           "methodRoundMax must be a whole number of 1 or more")

    def test_recurve_and_compound_disciplines_need_their_laws(self):
        self.assertProblem(self.broken("ARCHERY", lambda b: b.pop("recurveSetPoints")),
                           "discipline 'RM' is recurve, so recurveSetPoints.SINGLE.pairs must list the legal pairs",
                           "discipline 'RMT' is recurve, so recurveSetPoints.CREW.pairs")
        self.assertProblem(self.broken("ARCHERY", lambda b: b["disciplines"]["CM"].pop("maxTotal")),
                           "discipline 'CM' is compound, so it needs a maxTotal")

    def test_level_win_and_tiebreak_must_be_consistent(self):
        self.assertProblem(self.broken("HOCKEY", lambda b: b.update(levelWin="always")), "levelWin must be one of never, tiebreak, any")
        self.assertProblem(self.broken("HOCKEY", lambda b: b.pop("tiebreak")), "levelWin 'tiebreak' needs a tiebreak object")

    def test_votes_must_be_whole_numbers_in_order(self):
        self.assertProblem(self.broken("BOXING", lambda b: b.update(votes={"maxEach": 5, "totalMin": 6, "totalMax": 5})),
                           "votes must be {maxEach, totalMin, totalMax}")

    def test_continues_after_loss_must_name_the_sports_rounds(self):
        self.assertProblem(self.broken("WRESTLING", lambda b: b.update(continuesAfterLoss=[{"from": ["R16"], "to": ["REP9"]}])),
                           "continuesAfterLoss to must list rounds of the sport, got ['REP9']")

    def test_team_format_follows_the_sports_score_format(self):
        # score sports use 'score' for every discipline, team disciplines included ...
        self.assertProblem(self.broken("HOCKEY", lambda b: b.update(scoreFormatByDiscipline={"MT": "ties"})),
                           "rules.json: HOCKEY MT scoreFormat must be 'score', got 'ties'")
        # ... and racket sports keep 'ties' for MT, WT and XT, and 'games' for the rest.
        self.assertProblem(self.broken("BADMINTON", lambda b: b["scoreFormatByDiscipline"].update(MT="score")),
                           "rules.json: BADMINTON MT scoreFormat must be 'ties', got 'score'")
        self.assertProblem(self.broken("SQUASH", lambda b: b["scoreFormatByDiscipline"].pop("XT")),
                           "rules.json: SQUASH XT scoreFormat must be 'ties', got 'games'")
        self.assertProblem(self.broken("TABLE_TENNIS", lambda b: b["scoreFormatByDiscipline"].update(MS="score")),
                           "rules.json: TABLE_TENNIS MS scoreFormat must be 'games', got 'score'")

    def test_racket_sports_still_need_their_team_disciplines_listed(self):
        self.assertProblem(self.broken("SQUASH", lambda b: b.update(teamDisciplines=["MT", "WT"])),
                           "SQUASH teamDisciplines ['MT', 'WT'] must be ['MT', 'WT', 'XT']")


class ScoreDisciplineTest(ScoreCase):
    """Entries name their event: a code from the sport's list, with the name, gender and kind rules.json fixes."""

    def test_codes_with_a_plus_sign_are_accepted(self):
        self.assertClean(self.boxing([], BOX_MPLUS90))
        self.assertClean(self.boxing([], BOX_WPLUS80))

    def test_every_discipline_in_rules_json_accepts_its_own_name_gender_and_kind(self):
        for sport, rules in RULES.items():
            if rules["scoreFormat"] != "score":
                continue
            entries = []
            for code, rule in rules["disciplines"].items():
                spec = {"discipline": code, "disciplineName": rule["name"], "gender": rule["gender"], "kind": rule["kind"]}
                count = rule.get("athletes", {"SINGLE": 1, "PAIR": 2, "CREW": 3, "TEAM": 0}[rule["kind"]])
                entries.append(score_entry(spec, [], athletes=[f"Athlete {n}" for n in range(count)]))
            self.assertClean(score_errors([score_event(sport, entries)]))

    def test_unknown_code_is_rejected(self):
        self.assertOneProblem(self.boxing([], {**BOX_M60, "discipline": "M61"}), "discipline 'M61' not in allowed set")

    def test_a_racket_code_is_not_a_boxing_event(self):
        self.assertOneProblem(self.boxing([], {"discipline": "MS"}), "discipline 'MS' not in allowed set")

    def test_a_code_of_another_score_sport_is_rejected(self):
        self.assertOneProblem(self.wrestling([], BOX_M60), "discipline 'M60' not in allowed set")
        self.assertOneProblem(self.hockey([], ARC_RM), "discipline 'RM' not in allowed set")

    def test_the_name_must_be_the_one_rules_json_fixes(self):
        # "Men's 60kg" and "Men's 60 kg" from two sources must not split one event into two.
        self.assertOneProblem(self.boxing([], {**BOX_M60, "disciplineName": "Men's 60kg"}),
                              "disciplineName \"Men's 60kg\" must be \"Men's 60 kg\" for discipline 'M60'",
                              "one disciplineName per code")

    def test_name_gender_and_kind_are_required(self):
        for field, expected in (("disciplineName", '"Men\'s 60 kg"'), ("gender", "'M'"), ("kind", "'SINGLE'")):
            spec = {k: v for k, v in BOX_M60.items() if k != field}
            self.assertOneProblem(self.boxing([], spec), f"discipline 'M60' needs '{field}': {expected}",
                                  "the app skips the event without it")

    def test_gender_and_kind_must_match(self):
        self.assertOneProblem(self.boxing([], {**BOX_M60, "gender": "W"}), "gender 'W' must be 'M' for discipline 'M60'")
        self.assertOneProblem(self.boxing([], {**BOX_M60, "kind": "PAIR"}), "kind 'PAIR' must be 'SINGLE' for discipline 'M60'")

    def test_the_fields_are_checked_in_upcoming_events_too(self):
        ev = score_event("BOXING", [{"country": "IND", "athletes": ["Test Player"], "seed": None, **BOX_M60}])
        for field in ("status", "coverage", "medals", "timeZone"):
            del ev[field]
        ev.update(entriesPublished=True)
        errors = validate.Errors()
        doc = {"schemaVersion": 1, "updatedOn": ISO(TODAY), "sport": "BOXING", "events": [ev]}
        validate.validate_file("upcoming.json", doc, validate.UPCOMING_EVENT_REQUIRED, validate.UPCOMING_EVENT_NON_NULL,
                               RULES, errors, is_results=False)
        self.assertEqual(list(errors), [])
        ev["entries"][0]["disciplineName"] = "Men's 60kg"
        errors = validate.Errors()
        validate.validate_file("upcoming.json", doc, validate.UPCOMING_EVENT_REQUIRED, validate.UPCOMING_EVENT_NON_NULL,
                               RULES, errors, is_results=False)
        self.assertProblem(list(errors), "disciplineName \"Men's 60kg\" must be")

    def test_bare_team_codes_need_no_name_gender_or_kind(self):
        self.assertClean(self.hockey([], BARE_MT))
        self.assertClean(self.kabaddi([], BARE_WT))

    def test_bare_team_codes_ignore_the_three_fields_like_the_app(self):
        # the app reads MT as the racket event and ignores them, so a stray value is not worth a failed push
        self.assertClean(self.hockey([], {**BARE_MT, "disciplineName": "Whatever", "gender": "M", "kind": "TEAM"}))

    def test_racket_sports_are_unchanged_by_the_new_fields(self):
        # a racket entry still writes the bare code and may carry the fields (the app ignores them there)
        ev = event_base("bwf-results:test-open-2026", "BADMINTON",
                        [{**entry("MS", [match("R16", "WIN", [[21, 15], [21, 18]])]), "disciplineName": "x"}])
        self.assertClean(score_errors([ev]))
        ev["entries"][0]["discipline"] = "M60"
        self.assertProblem(score_errors([ev]), "discipline 'M60' not in allowed set")


class ScoreAthleteCountTest(ScoreCase):
    """The athlete count comes from the discipline's kind: SINGLE 1, PAIR 2, CREW 2 to 6, TEAM 0."""

    def test_single_has_one_athlete(self):
        self.assertClean(self.boxing([], athletes=["Sachin Siwach"]))
        self.assertOneProblem(self.boxing([], athletes=["A", "B"]), "discipline M60 (SINGLE) expects 1 athlete(s), got 2")
        self.assertOneProblem(self.boxing([], athletes=[]), "discipline M60 (SINGLE) expects 1 athlete(s), got 0")

    def test_athletes_must_be_a_list(self):
        self.assertOneProblem(self.boxing([], athletes="Sachin Siwach"), "athletes must be a list")

    def test_archery_teams_have_three_archers_and_mixed_teams_two(self):
        self.assertClean(self.archery([], ARC_RMT, athletes=["A", "B", "C"]))
        self.assertOneProblem(self.archery([], ARC_RMT, athletes=["A", "B"]), "discipline RMT (CREW) expects 3 athlete(s), got 2")
        self.assertOneProblem(self.archery([], ARC_RMT, athletes=["A", "B", "C", "D"]), "expects 3 athlete(s), got 4")
        self.assertClean(self.archery([], ARC_RXT, athletes=["A", "B"]))
        self.assertOneProblem(self.archery([], ARC_RXT, athletes=["A", "B", "C"]), "discipline RXT (CREW) expects 2 athlete(s), got 3")

    def test_team_entries_list_no_athletes(self):
        self.assertClean(self.hockey([], BARE_MT, athletes=[]))
        self.assertOneProblem(self.hockey([], BARE_MT, athletes=["A Player"]), "team entry athletes must be an empty list")
        self.assertOneProblem(self.kabaddi([], BARE_WT, athletes=["A Player"]), "team entry athletes must be an empty list")

    def test_pair_has_two_athletes(self):
        rules = edited_rules(lambda r: r["BOXING"]["disciplines"].update(P2={"name": "Pairs", "gender": "X", "kind": "PAIR"}))
        spec = {"discipline": "P2", "disciplineName": "Pairs", "gender": "X", "kind": "PAIR"}
        self.assertClean(self.errors_for("BOXING", spec, [], athletes=["A", "B"], rules=rules))
        self.assertOneProblem(self.errors_for("BOXING", spec, [], athletes=["A"], rules=rules),
                              "discipline P2 (PAIR) expects 2 athlete(s), got 1")

    def test_a_crew_without_a_pinned_size_has_two_to_six(self):
        rules = edited_rules(lambda r: r["ARCHERY"]["disciplines"].update(
            RX6={"name": "Relay", "gender": "X", "kind": "CREW"}))
        spec = {"discipline": "RX6", "disciplineName": "Relay", "gender": "X", "kind": "CREW"}
        for count in (2, 4, 6):
            self.assertClean(self.errors_for("ARCHERY", spec, [], athletes=[f"A{n}" for n in range(count)], rules=rules))
        for count in (1, 7):
            self.assertOneProblem(self.errors_for("ARCHERY", spec, [], athletes=[f"A{n}" for n in range(count)], rules=rules),
                                  f"discipline RX6 (CREW) expects 2 to 6 athlete(s), got {count}")


class ScoreShapeTest(ScoreCase):
    """A score match holds one pair of scores, or none."""

    def test_one_pair_or_none(self):
        self.assertClean(self.boxing([score_match("R32", "WIN", [[5, 0]], method="DEC")]))
        self.assertClean(self.boxing([score_match("R32", "WIN", [])]))
        self.assertOneProblem(self.hockey([team_match("G1", "WIN", [[2, 1], [3, 0]])]),
                              "a match holds one pair of scores or none, got 2 pairs")

    def test_the_pair_is_two_non_negative_whole_numbers(self):
        for bad in ([5], [5, 0, 1], [-1, 5], [1.5, 0], [True, 0], ["5", "0"], None):
            self.assertOneProblem(self.hockey([team_match("G1", "WIN", [bad])]),
                                  "scores[0]", "must be a pair [own, opponent] of non-negative whole numbers")

    def test_scores_that_are_not_a_list_are_reported_once(self):
        self.assertOneProblem(self.hockey([team_match("G1", "WIN", None)]), "scores must be a list")

    def test_no_score_for_a_walkover_a_bye_or_a_scheduled_match(self):
        self.assertClean(self.boxing([score_match("R32", "WALKOVER_WIN", [])]))
        self.assertClean(self.boxing([score_match("R32", "BYE", [], opponent=(), country=None)]))
        self.assertClean(self.hockey([team_match("G1", "SCHEDULED", [])], status="IN_PROGRESS"))
        for outcome in ("WALKOVER_WIN", "WALKOVER_LOSS", "BYE", "SCHEDULED"):
            self.assertOneProblem(self.hockey([team_match("G1", outcome, [[1, 0]])], status="IN_PROGRESS"),
                                  f"outcome {outcome} has no score, so scores must be []")

    def test_rubbers_do_not_belong_on_a_score_match(self):
        self.assertOneProblem(self.hockey([team_match("G1", "WIN", [[2, 1]], rubbers=[{"order": 1}])]),
                              "rubbers only belong on a team tie, not a MT match")

    def test_a_team_match_has_no_named_opponent(self):
        self.assertClean(self.hockey([team_match("G1", "WIN", [[2, 1]])]))
        self.assertOneProblem(self.hockey([score_match("G1", "WIN", [[2, 1]], opponent=("Some Team",))]),
                              "opponent must be empty for a team match (the opponent is a country)")

    def test_an_archery_team_match_may_name_the_opposing_archers_or_leave_them_out(self):
        win = [[235, 233]]
        self.assertClean(self.archery([score_match("F", "WIN", win, opponent=("A", "B", "C"))], ARC_CMT))
        self.assertClean(self.archery([score_match("F", "WIN", win, opponent=())], ARC_CMT))

    def test_outcomes_outside_the_sports_list_are_rejected(self):
        self.assertOneProblem(self.boxing([score_match("R32", "NOT_PLAYED", [])]), "outcome 'NOT_PLAYED' not in allowed set")
        self.assertOneProblem(self.boxing([score_match("R32", "DRAW", [[2, 2]])]), "outcome 'DRAW' not in allowed set")

    def test_rounds_outside_the_sports_list_are_rejected(self):
        self.assertOneProblem(self.boxing([score_match("R128", "WIN", [[3, 0]])]), "round 'R128' not in allowed set")
        self.assertOneProblem(self.kabaddi([team_match("G17", "WIN", [[30, 20]])]), "round 'G17' not in allowed set")


class WinnerAheadTest(ScoreCase):
    """A decided WIN or LOSS with a pair has the winner strictly ahead, with the exceptions in section 3."""

    def test_boxing_winner_is_strictly_ahead(self):
        self.assertClean(self.boxing([score_match("R32", "WIN", [[3, 2]], method="DEC")]))
        self.assertClean(self.boxing([score_match("R32", "LOSS", [[2, 3]], method="DEC")]))
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[2, 3]], method="DEC")]),
                              "outcome=WIN but our side is behind on the score 2-3", "the winner must be ahead")
        self.assertOneProblem(self.boxing([score_match("R32", "LOSS", [[3, 2]], method="DEC")]),
                              "outcome=LOSS but the opponent is behind on the score 3-2")

    def test_a_level_score_never_wins_in_boxing(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[2, 2]])]),
                              "outcome=WIN but the score 2-2 is level; the winner must be strictly ahead")

    def test_kabaddi_and_hockey_winner_is_ahead(self):
        self.assertClean(self.kabaddi([team_match("F", "WIN", [[37, 34]], country="IRI")]))
        self.assertOneProblem(self.kabaddi([team_match("F", "WIN", [[34, 37]], country="IRI")]),
                              "outcome=WIN but our side is behind on the score 34-37")
        self.assertClean(self.hockey([team_match("F", "WIN", [[5, 1]], country="MAS")]))
        self.assertOneProblem(self.hockey([team_match("F", "LOSS", [[5, 1]], country="MAS")]),
                              "outcome=LOSS but the opponent is behind on the score 5-1")

    def test_a_level_hockey_or_kabaddi_score_needs_a_tiebreak_or_a_draw(self):
        self.assertOneProblem(self.hockey([team_match("QF", "WIN", [[2, 2]])]),
                              "outcome=WIN with a level score 2-2 needs a tiebreak that decides it",
                              "a level group-round match is a DRAW")
        self.assertOneProblem(self.kabaddi([team_match("SF", "LOSS", [[30, 30]])]),
                              "with a level score 30-30 needs a tiebreak")
        self.assertClean(self.hockey([team_match("QF", "WIN", [[2, 2]], tiebreak=[4, 3])]))
        self.assertClean(self.kabaddi([team_match("SF", "LOSS", [[30, 30]], tiebreak=[3, 5])]))

    def test_wrestling_winner_is_at_least_level(self):
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[9, 8]])]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[4, 4]])]))  # criteria decide, such as the last point
        self.assertClean(self.wrestling([score_match("QF", "LOSS", [[4, 4]])]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[4, 4]], method="POINTS")]))

    def test_wrestling_winner_may_be_behind_only_by_fall_or_disqualification(self):
        for method in ("FALL", "DSQ"):
            self.assertClean(self.wrestling([score_match("QF", "WIN", [[2, 5]], method=method)]))
            self.assertClean(self.wrestling([score_match("QF", "LOSS", [[5, 2]], method=method)]))
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[2, 5]])]),
                              "outcome=WIN but our side is behind on the score 2-5",
                              "only a fall or a disqualification wins from behind")
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[2, 5]], method="POINTS")]),
                              "outcome=WIN but our side is behind")
        self.assertOneProblem(self.wrestling([score_match("QF", "LOSS", [[5, 2]], method="TECH_SUP")]),
                              "outcome=LOSS but the opponent is behind")

    def test_wrestling_margins_are_not_checked(self):
        # about 3% of real bouts break the textbook margins; the card shows what the page shows
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[10, 5]], method="TECH_SUP")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[2, 1]], method="TECH_SUP")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[13, 2]], method="POINTS")]))

    def test_an_injury_default_is_not_judged_on_the_points(self):
        # the injured wrestler may have been ahead when the bout stopped
        self.assertClean(self.wrestling([score_match("QF", "RETIRED_WIN", [[2, 5]], method="INJURY")]))
        self.assertClean(self.wrestling([score_match("QF", "RETIRED_WIN", [], method="INJURY")]))


class DrawTest(ScoreCase):
    """DRAW: a level pair, no tiebreak, a group round only, hockey and kabaddi only."""

    def test_a_group_draw_in_hockey_and_kabaddi_is_fine(self):
        self.assertClean(self.hockey([team_match("G3", "DRAW", [[3, 3]], country="KAZ")]))
        self.assertClean(self.kabaddi([team_match("G2", "DRAW", [[28, 28]])]))
        self.assertClean(self.hockey([team_match("G16", "DRAW", [[0, 0]])]))

    def test_a_draw_is_never_a_win_or_a_knockout_loss(self):
        # a group draw does not end the run; the next match is still allowed
        self.assertClean(self.hockey([team_match("G1", "DRAW", [[1, 1]]), team_match("SF", "WIN", [[2, 1]])]))

    def test_a_draw_needs_a_level_pair(self):
        self.assertOneProblem(self.hockey([team_match("G3", "DRAW", [[2, 1]])]), "a DRAW needs a level score, got 2-1")
        self.assertOneProblem(self.hockey([team_match("G3", "DRAW", [])]), "a DRAW needs a level score, but the match has none")

    def test_a_draw_has_no_tiebreak(self):
        self.assertOneProblem(self.hockey([team_match("G3", "DRAW", [[2, 2]], tiebreak=[3, 2])]),
                              "a DRAW has no tiebreak; a match decided by one is a WIN or LOSS")

    def test_a_draw_only_in_a_group_round(self):
        for rnd in ("QF", "SF", "F", "5P"):
            self.assertOneProblem(self.hockey([team_match(rnd, "DRAW", [[1, 1]])]),
                                  f"a DRAW is only allowed in a group round (G1 to G16), not in '{rnd}'")
        self.assertOneProblem(self.kabaddi([team_match("F", "DRAW", [[30, 30]])]), "a DRAW is only allowed in a group round")

    def test_other_sports_do_not_allow_a_draw(self):
        self.assertOneProblem(self.boxing([score_match("R32", "DRAW", [[2, 2]])]), "outcome 'DRAW' not in allowed set")
        self.assertOneProblem(self.wrestling([score_match("QF", "DRAW", [[1, 1]])]), "outcome 'DRAW' not in allowed set")
        self.assertOneProblem(self.archery([score_match("QF", "DRAW", [[1, 1]])]), "outcome 'DRAW' not in allowed set")

    def test_racket_sports_still_reject_a_draw(self):
        ev = event_base("bwf-results:test-open-2026", "BADMINTON", [entry("MS", [match("G1", "DRAW", [[21, 15]])])])
        self.assertProblem(score_errors([ev]), "outcome 'DRAW' not in allowed set")


class TiebreakTest(ScoreCase):
    """tiebreak [own, opponent]: only after a level score; the side that wins it wins the match."""

    def test_the_singapore_bangladesh_ninth_place_match(self):
        # Asian Games 2026 women's hockey, 9th place: 1-1, shootout 1-2 (Bangladesh won)
        self.assertClean(self.hockey([team_match("9P", "LOSS", [[1, 1]], country="BAN", tiebreak=[1, 2])], BARE_WT, country="SGP"))

    def test_the_winner_must_be_ahead_in_the_tiebreak(self):
        self.assertOneProblem(self.hockey([team_match("9P", "LOSS", [[1, 1]], country="BAN", tiebreak=[2, 1])], BARE_WT),
                              "outcome=LOSS but the shootout 2-1 goes to our side")
        self.assertOneProblem(self.hockey([team_match("9P", "WIN", [[1, 1]], country="BAN", tiebreak=[1, 2])], BARE_WT),
                              "outcome=WIN but the shootout 1-2 goes to the opponent")

    def test_a_hockey_or_kabaddi_tiebreak_has_a_winner(self):
        self.assertOneProblem(self.hockey([team_match("QF", "WIN", [[2, 2]], tiebreak=[3, 3])]),
                              "the shootout 3-3 is level; a shootout has a winner")
        self.assertOneProblem(self.kabaddi([team_match("SF", "WIN", [[30, 30]], tiebreak=[2, 2])]),
                              "the tie-break 2-2 is level; a tie-break has a winner")

    def test_only_after_a_level_score(self):
        self.assertOneProblem(self.hockey([team_match("QF", "WIN", [[3, 2]], tiebreak=[4, 3])]),
                              "tiebreak only after a level score, but the score is 3-2")
        self.assertOneProblem(self.hockey([team_match("QF", "WIN", [], tiebreak=[4, 3])]),
                              "tiebreak only after a level score, but the match has no score")
        self.assertOneProblem(self.hockey([team_match("G1", "WALKOVER_WIN", [], tiebreak=[4, 3])]),
                              "a shootout only decides a WIN or LOSS, not WALKOVER_WIN")

    def test_the_tiebreak_is_a_pair_of_whole_numbers(self):
        for bad in ([3], [3, 2, 1], [-1, 2], [1.5, 1], [True, 0], "3-2", None):
            self.assertOneProblem(self.hockey([team_match("QF", "WIN", [[2, 2]], tiebreak=bad)]),
                                  "tiebreak", "must be a pair [own, opponent] of non-negative whole numbers")

    def test_sports_without_a_tiebreak_reject_the_field(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[3, 2]], tiebreak=[1, 0])]), "tiebreak is not used in this sport")
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[4, 4]], tiebreak=[1, 0])]), "tiebreak is not used in this sport")

    def test_an_archery_shoot_off_may_be_level_and_the_outcome_says_who_won(self):
        # closest to the centre decides a shoot-off that is level on score
        self.assertClean(self.archery([score_match("F", "WIN", [[146, 146]], tiebreak=[10, 10])], ARC_CW))
        self.assertClean(self.archery([score_match("F", "LOSS", [[146, 146]], tiebreak=[10, 10])], ARC_CW))
        self.assertClean(self.archery([score_match("F", "WIN", [[146, 146]], tiebreak=[10, 9])], ARC_CW))
        self.assertOneProblem(self.archery([score_match("F", "WIN", [[146, 146]], tiebreak=[9, 10])], ARC_CW),
                              "outcome=WIN but the shoot-off 9-10 goes to the opponent")

    def test_a_level_compound_total_needs_a_tiebreak(self):
        # the 2025 Worlds men's team compound pairing with no shoot-off note on the page: 226-226
        self.assertOneProblem(self.archery([score_match("R16", "WIN", [[226, 226]])], ARC_CMT),
                              "with a level score 226-226 needs a tiebreak that decides it")
        self.assertClean(self.archery([score_match("R16", "WIN", [[232, 232]], tiebreak=[30, 28])], ARC_CMT))
        self.assertClean(self.archery([score_match("R16", "LOSS", [[155, 155]], tiebreak=[18, 20])], ARC_CXT))

    def test_a_recurve_shoot_off_pair_may_carry_the_arrows(self):
        # Asian Games 2026 women's individual final: Kumkum Mohod 6-5 on a shoot-off, arrows 10 against 8
        self.assertClean(self.archery([score_match("F", "WIN", [[6, 5]], country="KOR", tiebreak=[10, 8])]))
        self.assertClean(self.archery([score_match("F", "LOSS", [[5, 6]], country="KOR", tiebreak=[8, 10])]))
        self.assertClean(self.archery([score_match("F", "WIN", [[5, 4]], tiebreak=[29, 27])], ARC_RMT))
        self.assertClean(self.archery([score_match("F", "WIN", [[6, 5]])]))  # the pair already shows the shoot-off

    def test_a_recurve_tiebreak_goes_only_with_the_shoot_off_pair(self):
        self.assertOneProblem(self.archery([score_match("F", "WIN", [[6, 4]], tiebreak=[10, 8])]),
                              "tiebreak only after a level score, but the score is 6-4")
        # 5-4 is a team result; the individual pair of a shoot-off is 6-5
        self.assertProblem(self.archery([score_match("F", "WIN", [[5, 4]], tiebreak=[10, 8])]),
                           "tiebreak only after a level score, but the score is 5-4")


class MethodTest(ScoreCase):
    """`method` comes from the sport's list and must go with the outcome and the score."""

    def test_every_boxing_method_with_its_outcome_and_score(self):
        self.assertClean(self.boxing([score_match("R32", "WIN", [[5, 0]], method="DEC")]))
        self.assertClean(self.boxing([score_match("R32", "WIN", [], method="RSC", methodRound=2)]))
        self.assertClean(self.boxing([score_match("R32", "LOSS", [], method="RSC-I", methodRound=3)]))
        self.assertClean(self.boxing([score_match("R32", "WIN", [], method="KO", methodRound=1)]))
        self.assertClean(self.boxing([score_match("R32", "WIN", [], method="DSQ")]))
        self.assertClean(self.boxing([score_match("R32", "RETIRED_WIN", [], method="ABD")]))
        self.assertClean(self.boxing([score_match("R32", "RETIRED_LOSS", [], method="ABD")]))

    def test_a_method_the_sport_does_not_list_is_rejected(self):
        for bad in ("TKO", "WO", "dec", "VFA", "SHOOT_OFF"):
            self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[5, 0]], method=bad)]),
                                  f"method '{bad}' is not one of ['DEC', 'RSC', 'RSC-I', 'KO', 'DSQ', 'ABD']")

    def test_a_method_must_be_a_string_or_left_out(self):
        for bad in (5, None, ["DEC"], True):
            self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[5, 0]], method=bad)]),
                                  "method must be a string from ['DEC', 'RSC', 'RSC-I', 'KO', 'DSQ', 'ABD'] or left out")

    def test_sports_without_methods_reject_the_field(self):
        self.assertOneProblem(self.archery([score_match("F", "WIN", [[6, 4]], method="DEC")]),
                              "method 'DEC' is not used here: this sport has no methods")
        self.assertOneProblem(self.hockey([team_match("G1", "WIN", [[2, 1]], method="DEC")]), "this sport has no methods")

    def test_a_method_must_go_with_the_outcome(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [], method="ABD")]),
                              "method ABD (Abandon) does not go with outcome 'WIN'; it goes with RETIRED_WIN, RETIRED_LOSS")
        self.assertOneProblem(self.boxing([score_match("R32", "RETIRED_WIN", [[5, 0]], method="DEC")]),
                              "method DEC (Judges' decision) does not go with outcome 'RETIRED_WIN'")
        self.assertOneProblem(self.boxing([score_match("R32", "BYE", [], opponent=(), country=None, method="DSQ")]),
                              "method DSQ (Disqualification) does not go with outcome 'BYE'")
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[9, 1]], method="INJURY")]),
                              "method INJURY (Injury default) does not go with outcome 'WIN'")

    def test_a_boxing_decision_needs_the_votes(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [], method="DEC")]),
                              "method DEC (Judges' decision) is decided on a score, so scores must hold the pair")

    def test_a_stoppage_an_abandon_or_a_disqualification_has_empty_scores(self):
        for method, outcome, pair in (("RSC", "WIN", [3, 0]), ("RSC-I", "WIN", [3, 0]), ("KO", "LOSS", [0, 3]),
                                      ("DSQ", "WIN", [3, 0]), ("ABD", "RETIRED_WIN", [3, 0])):
            self.assertOneProblem(self.boxing([score_match("R32", outcome, [pair], method=method)]),
                                  f"method {method} (", f"has no score, so scores must be [] (got [{pair}])")

    def test_wrestling_methods(self):
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[8, 1]], method="FALL")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [], method="FALL")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[12, 0]], method="TECH_SUP")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[6, 4]], method="POINTS")]))
        self.assertClean(self.wrestling([score_match("QF", "RETIRED_WIN", [[5, 0]], method="INJURY")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[0, 0]], method="DSQ")]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [], method="DSQ")]))

    def test_wrestling_rejects_the_uww_codes_and_mismatched_methods(self):
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[8, 1]], method="VFA")]), "method 'VFA' is not one of")
        self.assertOneProblem(self.wrestling([score_match("QF", "RETIRED_WIN", [[8, 1]], method="FALL")]),
                              "method FALL (Win by fall) does not go with outcome 'RETIRED_WIN'")

    def test_wrestling_points_and_technical_superiority_are_decided_on_the_score(self):
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [], method="POINTS")]), "method POINTS (Win on points) is decided on a score")
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [], method="TECH_SUP")]), "method TECH_SUP (Technical superiority) is decided on a score")

    def test_a_walkover_has_no_method(self):
        self.assertProblem(self.boxing([score_match("R32", "WALKOVER_WIN", [], method="DEC")]),
                           "method DEC (Judges' decision) does not go with outcome 'WALKOVER_WIN'")


class BoxingVotesTest(ScoreCase):
    """Judges' votes: each side 0 to 5, a total of 3 to 5, the winner strictly ahead."""

    def test_legal_votes(self):
        for votes in ([5, 0], [4, 1], [3, 2], [3, 0], [4, 0], [2, 1], [5, 0]):
            self.assertClean(self.boxing([score_match("R32", "WIN", [votes], method="DEC")]))
            self.assertClean(self.boxing([score_match("R32", "LOSS", [votes[::-1]], method="DEC")]))

    def test_each_side_is_at_most_five(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[6, 0]], method="DEC")]),
                              "judges' votes must each be 0 to 5, got 6-0")

    def test_the_total_is_three_to_five(self):
        for votes, total in (([1, 0], 1), ([2, 0], 2), ([4, 2], 6), ([5, 1], 6), ([5, 4], 9)):
            self.assertOneProblem(self.boxing([score_match("R32", "WIN", [votes], method="DEC")]),
                                  f"judges' votes must total 3 to 5, got {votes[0]}-{votes[1]} (total {total})")

    def test_the_votes_apply_even_when_the_method_is_not_recorded(self):
        # the method is written only when the page states it; a bare 5-0 cell is still judges' votes
        self.assertClean(self.boxing([score_match("R32", "WIN", [[5, 0]])]))
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[7, 0]])]), "judges' votes must each be 0 to 5")

    def test_wrestling_points_are_not_votes(self):
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[10, 0]])]))

    def test_method_round(self):
        for rnd in (1, 2, 3):
            self.assertClean(self.boxing([score_match("R32", "WIN", [], method="RSC", methodRound=rnd)]))
        for bad in (0, 4, -1, "2", 2.0, True, None):
            self.assertOneProblem(self.boxing([score_match("R32", "WIN", [], method="RSC", methodRound=bad)]),
                                  f"methodRound must be a whole number from 1 to 3, got {bad!r}")

    def test_method_round_goes_only_with_a_stoppage(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[5, 0]], method="DEC", methodRound=2)]),
                              "methodRound goes only with method RSC, RSC-I, KO, not with DEC")
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [], methodRound=2)]),
                              "methodRound goes only with method RSC, RSC-I, KO, not with no method")

    def test_method_round_is_a_boxing_field(self):
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[8, 1]], method="FALL", methodRound=2)]),
                              "methodRound is not used in this sport")
        self.assertOneProblem(self.hockey([team_match("G1", "WIN", [[2, 1]], methodRound=2)]), "methodRound is not used in this sport")


class WrestlingMethodTimeTest(ScoreCase):
    """methodTime "m:ss": the time of a fall or technical superiority, wrestling only."""

    def test_the_time_of_a_fall(self):
        for time in ("2:31", "0:04", "6:00", "0:59", "12:05"):
            self.assertClean(self.wrestling([score_match("QF", "WIN", [[8, 1]], method="FALL", methodTime=time)]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[14, 2]], method="TECH_SUP", methodTime="3:12")]))

    def test_the_time_must_look_like_m_ss(self):
        for bad in ("02:31", "00:04", "2:5", "2:60", "2.31", "231", " 2:31", "2:31 ", "2:31\n", "٢:٣١", 151, None):
            self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[8, 1]], method="FALL", methodTime=bad)]),
                                  f"methodTime {bad!r} must look like m:ss, for example '2:31' or '0:04'")

    def test_the_time_goes_only_with_a_fall_or_technical_superiority(self):
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[6, 4]], method="POINTS", methodTime="6:00")]),
                              "methodTime goes only with method FALL, TECH_SUP, not with POINTS")
        self.assertOneProblem(self.wrestling([score_match("QF", "WIN", [[6, 4]], methodTime="6:00")]),
                              "methodTime goes only with method FALL, TECH_SUP, not with no method")

    def test_the_time_is_a_wrestling_field(self):
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [], method="KO", methodRound=1, methodTime="2:31")]),
                              "methodTime is not used in this sport")
        self.assertOneProblem(self.archery([score_match("F", "WIN", [[6, 4]], methodTime="2:31")]), "methodTime is not used in this sport")


class ArcheryScoreTest(ScoreCase):
    """Recurve set points (individual to 6, team to 5) and compound totals (150, 240, 160)."""

    individual = [[6, 0], [6, 2], [6, 4], [7, 1], [7, 3], [6, 5]]
    team = [[6, 0], [5, 1], [6, 2], [5, 3], [5, 4]]

    def test_recurve_individual_legal_pairs(self):
        for pair in self.individual:
            self.assertClean(self.archery([score_match("QF", "WIN", [pair])], ARC_RM))
            self.assertClean(self.archery([score_match("QF", "LOSS", [pair[::-1]])], ARC_RW))

    def test_recurve_individual_illegal_pairs(self):
        for pair in ([6, 1], [6, 3], [7, 0], [7, 2], [7, 4], [8, 0], [8, 2], [5, 1], [5, 3], [5, 4], [4, 0]):
            self.assertOneProblem(self.archery([score_match("QF", "WIN", [pair])], ARC_RM),
                                  f"recurve set points {pair[0]}-{pair[1]} are not a legal individual result",
                                  "6-0, 6-2, 6-4, 7-1, 7-3, 6-5")

    def test_recurve_team_legal_pairs(self):
        for spec in (ARC_RMT, ARC_RXT):
            for pair in self.team:
                self.assertClean(self.archery([score_match("QF", "WIN", [pair])], spec))
                self.assertClean(self.archery([score_match("QF", "LOSS", [pair[::-1]])], spec))

    def test_recurve_team_illegal_pairs(self):
        # 6-4 and 7-1 are individual results; a team (or mixed team) match ends at 5 set points
        for pair in ([6, 4], [7, 1], [7, 3], [5, 2], [6, 1], [6, 3], [5, 0], [4, 2]):
            self.assertOneProblem(self.archery([score_match("QF", "WIN", [pair])], ARC_RMT),
                                  f"recurve set points {pair[0]}-{pair[1]} are not a legal team result", "6-0, 5-1, 6-2, 5-3, 5-4")
        self.assertProblem(self.archery([score_match("QF", "WIN", [[6, 4]])], ARC_RXT), "not a legal team result")

    def test_a_level_recurve_pair_is_not_a_result(self):
        self.assertProblem(self.archery([score_match("QF", "WIN", [[5, 5]], tiebreak=[10, 9])], ARC_RM),
                           "recurve set points 5-5 are not a legal individual result")

    def test_compound_totals_within_the_maxima(self):
        self.assertClean(self.archery([score_match("QF", "WIN", [[150, 149]])], ARC_CM))
        self.assertClean(self.archery([score_match("QF", "WIN", [[240, 238]])], ARC_CMT))
        self.assertClean(self.archery([score_match("QF", "WIN", [[160, 155]])], ARC_CXT))
        self.assertClean(self.archery([score_match("QF", "LOSS", [[0, 148]])], ARC_CW))

    def test_compound_totals_above_the_maxima_are_rejected(self):
        self.assertOneProblem(self.archery([score_match("QF", "WIN", [[151, 149]])], ARC_CM),
                              "compound total 151 is above the 150 points a Compound men can score")
        self.assertOneProblem(self.archery([score_match("QF", "WIN", [[241, 238]])], ARC_CMT),
                              "compound total 241 is above the 240 points a Compound men's team can score")
        self.assertOneProblem(self.archery([score_match("QF", "LOSS", [[161, 162]])], ARC_CXT),
                              "compound total 162 is above the 160 points a Compound mixed team can score")
        # a team total is too much for one archer, and a mixed team shoots fewer arrows than a team
        self.assertOneProblem(self.archery([score_match("QF", "WIN", [[235, 233]])], ARC_CM), "above the 150 points")
        self.assertOneProblem(self.archery([score_match("QF", "WIN", [[235, 233]])], ARC_CXT), "above the 160 points")

    def test_archery_has_no_methods(self):
        self.assertClean(self.archery([score_match("F", "WIN", [[6, 4]])]))

    def test_the_laws_of_the_other_sports_do_not_leak_in(self):
        # boxing votes and hockey goals are not set points: only archery's discipline picks recurve or compound
        self.assertClean(self.hockey([team_match("G1", "WIN", [[19, 0]])]))
        self.assertClean(self.kabaddi([team_match("G1", "WIN", [[89, 23]])]))
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[33, 2]])]))


class ScoreProgressionTest(ScoreCase):
    """A knockout loss ends the run, except where the sport's rules.json lets it continue."""

    def test_group_rounds_are_g_plus_one_or_two_digits(self):
        for rnd in ("G1", "G9", "G10", "G16"):
            self.assertIsNotNone(validate.GROUP_ROUND_RE.fullmatch(rnd), rnd)
        for rnd in ("G", "G100", "g1", "G1x", "QG1", "G-1"):
            self.assertIsNone(validate.GROUP_ROUND_RE.fullmatch(rnd), rnd)

    def test_a_pro_league_season_of_sixteen_group_matches(self):
        season = [team_match(f"G{n}", "LOSS" if n % 3 == 0 else "WIN", [[1, 2]] if n % 3 == 0 else [[2, 1]]) for n in range(1, 17)]
        self.assertClean(self.hockey(season))
        self.assertClean(self.kabaddi(season[:5] + [team_match("SF", "WIN", [[30, 20]])]))

    def test_racket_sports_still_stop_at_g5(self):
        ev = event_base("bwf-results:test-open-2026", "BADMINTON", [entry("MS", [match("G10", "WIN", [[21, 15], [21, 18]])])])
        self.assertProblem(score_errors([ev]), "round 'G10' not in allowed set")

    def test_boxing_has_no_third_place_match_and_a_loss_ends_the_run(self):
        self.assertOneProblem(self.boxing([score_match("SF", "LOSS", [[1, 4]]), score_match("3P", "WIN", [[4, 1]])]),
                              "round '3P' not in allowed set")
        self.assertOneProblem(self.boxing([score_match("R16", "LOSS", [[1, 4]]), score_match("QF", "WIN", [[4, 1]])]),
                              "comes after a knockout loss")
        self.assertClean(self.boxing([score_match("R32", "WIN", [[5, 0]]), score_match("R16", "LOSS", [[1, 4]])]))

    def test_kabaddi_has_no_third_place_match(self):
        self.assertOneProblem(self.kabaddi([team_match("SF", "LOSS", [[30, 33]]), team_match("3P", "WIN", [[33, 30]])]),
                              "round '3P' not in allowed set")
        self.assertClean(self.kabaddi([team_match("G1", "WIN", [[40, 30]]), team_match("SF", "LOSS", [[30, 33]])]))

    def test_wrestling_repechage_follows_an_early_loss(self):
        # Shokhida Akhmedova, 2025 Worlds women's 53 kg: lost to the eventual champion, then won two repechage bouts
        path = [score_match("R32", "LOSS", [[0, 11]]), score_match("REP1", "WIN", [[8, 4]]),
                score_match("REP2", "WIN", [[8, 1]], method="FALL"), score_match("3P", "LOSS", [[0, 8]], method="FALL")]
        self.assertClean(self.wrestling(path))
        # Laura Herin went straight to the second repechage round, so REP1 is not required
        self.assertClean(self.wrestling([score_match("R16", "LOSS", [[4, 5]], method="FALL"), score_match("REP2", "LOSS", [[1, 9]])]))

    def test_wrestling_semi_final_loser_wrestles_for_bronze(self):
        self.assertClean(self.wrestling([score_match("QF", "WIN", [[9, 8]]), score_match("SF", "LOSS", [[3, 5]]),
                                         score_match("3P", "WIN", [[9, 1]])]))

    def test_a_repechage_or_bronze_loss_ends_the_run(self):
        self.assertOneProblem(self.wrestling([score_match("R16", "LOSS", [[4, 5]]), score_match("REP1", "LOSS", [[1, 9]]),
                                              score_match("REP2", "WIN", [[9, 1]])]),
                              "match[2] REP2 (WIN) comes after a knockout loss in match[1] REP1 (LOSS)")
        self.assertOneProblem(self.wrestling([score_match("R16", "LOSS", [[4, 5]]), score_match("REP2", "LOSS", [[1, 9]]),
                                              score_match("3P", "WIN", [[9, 1]])]),
                              "match[2] 3P (WIN) comes after a knockout loss in match[1] REP2 (LOSS)")
        self.assertOneProblem(self.wrestling([score_match("3P", "LOSS", [[1, 9]]), score_match("REP1", "WIN", [[9, 1]])]),
                              "match[1] REP1 (WIN) comes after a knockout loss in match[0] 3P (LOSS)")

    def test_a_wrestling_loss_does_not_allow_another_main_bracket_bout(self):
        self.assertOneProblem(self.wrestling([score_match("R16", "LOSS", [[4, 5]]), score_match("QF", "WIN", [[9, 1]])]),
                              "match[1] QF (WIN) comes after a knockout loss in match[0] R16 (LOSS)")
        self.assertOneProblem(self.wrestling([score_match("SF", "LOSS", [[3, 5]]), score_match("REP1", "WIN", [[9, 1]])]),
                              "match[1] REP1 (WIN) comes after a knockout loss in match[0] SF (LOSS)")
        self.assertOneProblem(self.wrestling([score_match("F", "LOSS", [[0, 5]]), score_match("3P", "WIN", [[9, 1]])]),
                              "comes after a knockout loss in match[0] F (LOSS)")

    def test_hockey_classification_matches_follow_a_knockout_loss(self):
        self.assertClean(self.hockey([team_match("QF", "LOSS", [[1, 2]]), team_match("5P", "WIN", [[3, 1]])]))
        for rnd in ("5P", "7P", "9P", "11P", "13P", "15P"):
            self.assertClean(self.hockey([team_match("QF", "LOSS", [[1, 2]]), team_match(rnd, "LOSS", [[1, 2]])]))

    def test_the_singapore_women_play_the_ninth_place_match_after_pool_losses(self):
        pool = [team_match(f"G{n}", "LOSS", [[0, n]], country="IND") for n in range(1, 6)]
        self.assertClean(self.hockey(pool + [team_match("9P", "LOSS", [[1, 1]], country="BAN", tiebreak=[1, 2])], BARE_WT, country="SGP"))

    def test_a_classification_loss_ends_the_run(self):
        self.assertOneProblem(self.hockey([team_match("QF", "LOSS", [[1, 2]]), team_match("5P", "LOSS", [[1, 2]]),
                                           team_match("7P", "WIN", [[2, 1]])]),
                              "match[2] 7P (WIN) comes after a knockout loss in match[1] 5P (LOSS)")

    def test_hockey_semi_final_loser_plays_bronze_and_a_bronze_loss_ends_the_run(self):
        self.assertClean(self.hockey([team_match("SF", "LOSS", [[1, 2]]), team_match("3P", "WIN", [[2, 1]])]))
        self.assertOneProblem(self.hockey([team_match("3P", "LOSS", [[1, 2]]), team_match("5P", "WIN", [[2, 1]])]),
                              "match[1] 5P (WIN) comes after a knockout loss in match[0] 3P (LOSS)")
        self.assertOneProblem(self.hockey([team_match("QF", "LOSS", [[1, 2]]), team_match("SF", "WIN", [[2, 1]])]),
                              "match[1] SF (WIN) comes after a knockout loss in match[0] QF (LOSS)")

    def test_archery_semi_final_loser_shoots_for_bronze(self):
        # Parneet Kaur, 2025 Worlds compound women: won the quarter-final, lost the semi-final and the bronze match
        self.assertClean(self.archery([score_match("QF", "WIN", [[149, 147]], country="IND"), score_match("SF", "LOSS", [[142, 143]]),
                                       score_match("3P", "LOSS", [[144, 145]])], ARC_CW))
        self.assertOneProblem(self.archery([score_match("QF", "LOSS", [[142, 143]]), score_match("3P", "WIN", [[145, 144]])], ARC_CW),
                              "match[1] 3P (WIN) comes after a knockout loss in match[0] QF (LOSS)")

    def test_a_draw_or_a_scheduled_match_after_a_loss_is_not_a_decided_match(self):
        self.assertClean(self.hockey([team_match("QF", "LOSS", [[1, 2]]), team_match("5P", "SCHEDULED", [])], status="IN_PROGRESS"))


class ScoreCountryCodeTest(ScoreCase):
    """Country codes stay IOC only; the routine maps Wikipedia's ISO-style codes."""

    def test_ioc_codes_pass(self):
        self.assertClean(self.kabaddi([team_match("F", "WIN", [[37, 34]], country="IRI")]))
        self.assertClean(self.hockey([team_match("G1", "WIN", [[3, 1]], country="IRL")]))
        self.assertClean(self.hockey([team_match("G1", "WIN", [[3, 1]], country="JPN")]))
        self.assertClean(self.boxing([score_match("R32", "WIN", [[5, 0]], country="AIN")]))

    def test_iso_style_codes_are_rejected(self):
        for bad, ioc in (("IRN", "IRI"), ("IRE", "IRL"), ("JAP", "JPN"), ("PRT", "POR"), ("SIN", "SGP")):
            self.assertOneProblem(self.hockey([team_match("G1", "WIN", [[3, 1]], country=bad)]), f"country {bad!r} is not an IOC code")
        self.assertOneProblem(self.kabaddi([team_match("G1", "WIN", [[3, 1]], country="HK")]), "must be exactly 3 uppercase letters")
        self.assertOneProblem(self.boxing([score_match("R32", "WIN", [[5, 0]])], country="IRN"), "country 'IRN' is not an IOC code")


class RealScoreMatchesTest(Base):
    """Real results from the probes' Wikipedia pages, through `python3 validate.py` (each with a mistyped twin)."""

    def run_events(self, *events):
        w = self.ws()
        w.results["events"].extend(events)
        return w.run()

    def boxing_worlds(self, siwach_votes=([5, 0], [1, 4])):
        # 2025 World Boxing Championships, men's 60 kg (43 entrants, so the preliminaries were R64)
        siwach = score_entry(BOX_M60, [
            score_match("R32", "WIN", [siwach_votes[0]], country="AUS", opponent=("Jacob Cassar",), method="DEC", date="2025-09-06"),
            score_match("R16", "LOSS", [siwach_votes[1]], country="KAZ", opponent=("Biibars Zhexen",), method="DEC", date="2025-09-08"),
        ], athletes=["Sachin Siwach"])
        return score_event("BOXING", [siwach], event_id="boxing-results:world-championships-2025", medals=True,
                           timeZone="Europe/London")

    def test_sachin_siwach_beats_jacob_cassar_5_0_then_loses_1_4_to_biibars_zhexen(self):
        self.assertOk(self.run_events(self.boxing_worlds()))

    def test_the_same_bouts_with_the_votes_mistyped_are_refused(self):
        self.assertFails(self.run_events(self.boxing_worlds(siwach_votes=([5, 0], [1, 5]))),
                         "judges' votes must total 3 to 5, got 1-5 (total 6)")
        self.assertFails(self.run_events(self.boxing_worlds(siwach_votes=([0, 5], [1, 4]))),
                         "outcome=WIN but our side is behind on the score 0-5")

    def kabaddi_final(self, scores):
        # Asian Games 2026 women's kabaddi final: India 37, Iran 34
        ours = score_entry(BARE_WT, [team_match("F", "WIN", scores, country="IRI")])
        return score_event("KABADDI", [ours], event_id="kabaddi-results:asian-games-2026-womens", medals=True,
                           timeZone="Asia/Tokyo", tournament={"id": "asian-games-2026", "name": "Asian Games 2026", "shortName": "Asian Games"})

    def test_asian_games_womens_kabaddi_final_india_37_iran_34(self):
        self.assertOk(self.run_events(self.kabaddi_final([[37, 34]])))
        self.assertFails(self.run_events(self.kabaddi_final([[34, 37]])), "outcome=WIN but our side is behind on the score 34-37")

    def hockey_final(self, country):
        # Asian Games 2026 men's hockey final, 3 Oct, 19:00 JST: India 5, Malaysia 1
        ours = score_entry(BARE_MT, [team_match("F", "WIN", [[5, 1]], country=country, date="2026-10-03", time="19:00")])
        return score_event("HOCKEY", [ours], event_id="hockey-results:asian-games-2026-mens", medals=True, timeZone="Asia/Tokyo")

    def test_asian_games_mens_hockey_final_india_5_malaysia_1(self):
        self.assertOk(self.run_events(self.hockey_final("MAS")))
        self.assertFails(self.run_events(self.hockey_final("MYS")), "country 'MYS' is not an IOC code")

    def singapore_ninth(self, shootout):
        # Asian Games 2026 women's hockey, 9th place: Singapore 1-1 Bangladesh, shootout 1-2
        sgp = score_entry(BARE_WT, [team_match("9P", "LOSS", [[1, 1]], country="BAN", tiebreak=shootout)], country="SGP")
        return score_event("HOCKEY", [sgp], event_id="hockey-results:asian-games-2026-womens", medals=True, timeZone="Asia/Tokyo")

    def test_singapore_v_bangladesh_ninth_place_1_1_shootout_1_2(self):
        self.assertOk(self.run_events(self.singapore_ninth([1, 2])))
        self.assertFails(self.run_events(self.singapore_ninth([2, 1])), "outcome=LOSS but the shootout 2-1 goes to our side")

    def archery_individual(self, final_pair, shoot_off):
        # Asian Games 2026 recurve women: Kumkum Mohod beat Oh Yejin 6-5 in the final, shoot-off arrows 10 against 8
        mohod = score_entry(ARC_RW, [score_match("F", "WIN", [final_pair], country="KOR", opponent=("Oh Yejin",), tiebreak=shoot_off)],
                            athletes=["Kumkum Mohod"])
        return score_event("ARCHERY", [mohod], event_id="archery-results:asian-games-2026", medals=True, timeZone="Asia/Tokyo")

    def test_kumkum_mohod_wins_the_recurve_final_6_5_on_a_shoot_off(self):
        self.assertOk(self.run_events(self.archery_individual([6, 5], [10, 8])))
        self.assertFails(self.run_events(self.archery_individual([6, 1], [10, 8])), "recurve set points 6-1 are not a legal individual result")

    def test_the_compound_women_bracket_from_the_2025_worlds(self):
        kaur = score_entry(ARC_CW, [
            score_match("QF", "WIN", [[149, 147]], country="IND", opponent=("Jyothi Surekha Vennam",)),
            score_match("SF", "LOSS", [[142, 143]], country=None, opponent=()),
            score_match("3P", "LOSS", [[144, 145]], country=None, opponent=()),
        ], athletes=["Parneet Kaur"])
        team = score_entry(ARC_CMT, [score_match("F", "WIN", [[235, 233]], country="FRA", opponent=())])
        # the page's team shoot-off (232 with 30 against 232 with 28); the probe does not record which countries
        shoot_off = score_entry(ARC_CMT, [score_match("R16", "WIN", [[232, 232]], country="KOR", opponent=(), tiebreak=[30, 28])],
                                country="IND")
        event = score_event("ARCHERY", [kaur, team, shoot_off], event_id="archery-results:world-championships-2025", medals=True,
                            timeZone="Asia/Seoul")
        self.assertOk(self.run_events(event))
        shoot_off["matches"][0].pop("tiebreak")
        self.assertFails(self.run_events(event), "with a level score 232-232 needs a tiebreak that decides it")

    def wrestling_53kg(self, herin_fall_score):
        # 2025 World Championships, women's freestyle 53 kg (Zagreb, 17 and 18 September)
        panghal = score_entry(WRE_WW53, [
            score_match("R32", "BYE", [], country=None, opponent=()),
            score_match("R16", "WIN", [[10, 0]], country="ESP", opponent=("Carla Jaume",)),
            score_match("QF", "WIN", [[9, 8]], country="CHN", opponent=("Zhang Jin",)),
            score_match("SF", "LOSS", [[3, 5]], country="ECU", opponent=("Lucía Yépez",)),
            score_match("3P", "WIN", [[9, 1]], country="SWE", opponent=("Jonna Malmgren",)),
        ], athletes=["Antim Panghal"])
        akhmedova = score_entry(WRE_WW53, [
            score_match("R32", "LOSS", [[0, 11]], country="JPN", opponent=("Haruna Murayama",)),
            score_match("REP1", "WIN", [[8, 4]], country="GER", opponent=("Annika Wendle",)),
            score_match("REP2", "WIN", [[8, 1]], country="TUR", opponent=("Zeynep Yetgil",), method="FALL"),
            score_match("3P", "LOSS", [[0, 8]], country="PRK", opponent=("Choe Hyo-gyong",), method="FALL"),
        ], athletes=["Shokhida Akhmedova"], country="UZB")
        herin = score_entry(WRE_WW53, [
            score_match("R16", "LOSS", [herin_fall_score], country="ECU", opponent=("Lucía Yépez",), method="FALL"),
            score_match("REP2", "LOSS", [[1, 9]], country="SWE", opponent=("Jonna Malmgren",)),
        ], athletes=["Laura Herin"], country="CUB")
        return score_event("WRESTLING", [panghal, akhmedova, herin], event_id="wrestling-results:world-championships-2025",
                           medals=True, timeZone="Europe/Zagreb")

    def test_the_wrestling_53kg_bracket_with_its_repechage(self):
        self.assertOk(self.run_events(self.wrestling_53kg([4, 5])))

    def test_a_fall_can_be_lost_while_ahead_but_only_with_the_method(self):
        # Herin trailed Yepez 4-5 and was pinned; had a page shown her ahead 5-4 and still losing, only a fall explains it
        self.assertOk(self.run_events(self.wrestling_53kg([5, 4])))
        event = self.wrestling_53kg([5, 4])
        event["entries"][2]["matches"][0].pop("method")
        self.assertFails(self.run_events(event), "outcome=LOSS but the opponent is behind on the score 5-4")

    def test_the_bracket_without_its_method_is_still_fine_when_the_loser_trails(self):
        event = self.wrestling_53kg([4, 5])
        event["entries"][2]["matches"][0].pop("method")  # losing 4-5 needs no method, and none is guessed
        self.assertOk(self.run_events(event))
        event["entries"][2]["matches"][0].update(outcome="WIN")
        self.assertFails(self.run_events(event), "outcome=WIN but our side is behind on the score 4-5")

    def test_a_boxing_event_in_upcoming_json_checks_its_entries(self):
        w = self.ws()
        ev = score_event("BOXING", [{"country": "IND", "athletes": ["Sachin Siwach"], "seed": None, **BOX_M60}],
                         event_id="boxing-upcoming:world-championships-2027")
        for field in ("status", "coverage", "medals", "timeZone"):
            del ev[field]
        ev.update(entriesPublished=True, start=ISO(TODAY + timedelta(days=10)), end=ISO(TODAY + timedelta(days=15)))
        w.upcoming["events"].append(ev)
        self.assertOk(w.run())
        ev["entries"][0]["disciplineName"] = "Men's 60kg"
        self.assertFails(w.run(), "upcoming.json", "disciplineName \"Men's 60kg\" must be \"Men's 60 kg\"")

    def test_racket_data_next_to_score_data_is_judged_by_its_own_sport(self):
        w = self.ws()
        w.results["events"].append(self.boxing_worlds())
        self.assertOk(w.run())
        w.badminton_matches()[0]["scores"] = [[30, 2], [21, 10]]  # a racket game law still applies
        self.assertFails(w.run(), "game 1 score 30-2 is not a legal game")


if __name__ == "__main__":
    unittest.main()
