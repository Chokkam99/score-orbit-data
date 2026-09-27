#!/usr/bin/env python3
"""Validate upcoming.json and results.json against schema v1 and rules.json.

Generic (sport-agnostic) checks live in this file: JSON parsing, schemaVersion,
required fields, ISO dates, start <= end, sources with url + readOn, no em dash,
no SCHEDULED matches in FINISHED events, country-code shape, and the
win/loss-vs-games-won consistency check.

Sport-specific vocabulary (disciplines, rounds, levels, ageCategories, outcomes,
statuses, score format, max athletes per discipline) lives in rules.json, keyed
by the event's own "sport" field. Adding a new sport means adding an entry to
rules.json, not editing this file. Adding a new country needs no change at all:
any 3 uppercase letters are accepted for `country` / `opponentCountry`.

Exits non-zero with a clear message on the first class of failures found (all
errors for a file are collected and reported together).
"""
import json
import re
import sys
from datetime import date

EM_DASH = "—"
COUNTRY_RE = re.compile(r"^[A-Z]{3}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

UPCOMING_EVENT_REQUIRED = [
    "id", "name", "shortName", "sport", "ageCategory", "level",
    "officialTierName", "location", "start", "end", "entriesPublished",
    "entries", "sources",
]
RESULTS_EVENT_REQUIRED = [
    "id", "name", "shortName", "sport", "ageCategory", "level",
    "officialTierName", "location", "start", "end", "status",
    "coverage", "entries", "sources",
]
ENTRY_REQUIRED_COMMON = ["country", "discipline", "athletes"]
MATCH_REQUIRED = [
    "round", "outcome", "opponent", "opponentCountry", "scores",
]
SOURCE_REQUIRED = ["url", "readOn"]


class Errors(list):
    def add(self, msg):
        self.append(msg)


def load_json(path, errors):
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        errors.add(f"{path}: file not found")
        return None
    if EM_DASH in raw:
        idx = raw.index(EM_DASH)
        line = raw.count("\n", 0, idx) + 1
        errors.add(f"{path}: contains an em dash (U+2014) at line {line}; use a hyphen or rewrite")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        errors.add(f"{path}: invalid JSON - {e}")
        return None


def check_iso_date(path, ctx, value, errors, allow_none=False):
    if value is None:
        if not allow_none:
            errors.add(f"{path}: {ctx} is null but a date is required")
        return None
    if not isinstance(value, str) or not ISO_DATE_RE.match(value):
        errors.add(f"{path}: {ctx} = {value!r} is not an ISO date (YYYY-MM-DD)")
        return None
    try:
        y, m, d = (int(x) for x in value.split("-"))
        return date(y, m, d)
    except ValueError:
        errors.add(f"{path}: {ctx} = {value!r} is not a real calendar date")
        return None


def check_sources(path, ctx, sources, errors):
    if not isinstance(sources, list) or len(sources) == 0:
        errors.add(f"{path}: {ctx} must have at least one source")
        return
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            errors.add(f"{path}: {ctx} source[{i}] is not an object")
            continue
        for field in SOURCE_REQUIRED:
            if field not in src or not src[field]:
                errors.add(f"{path}: {ctx} source[{i}] missing '{field}'")
        if "readOn" in src and src["readOn"]:
            check_iso_date(path, f"{ctx} source[{i}].readOn", src["readOn"], errors)


def check_scores(path, ctx, scores, errors):
    if not isinstance(scores, list):
        errors.add(f"{path}: {ctx} scores must be a list")
        return
    for i, game in enumerate(scores):
        if (not isinstance(game, list)) or len(game) != 2:
            errors.add(f"{path}: {ctx} scores[{i}] must be a pair [own, opponent]")
            continue
        for v in game:
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                errors.add(f"{path}: {ctx} scores[{i}] value {v!r} is not a non-negative int")


def check_win_loss_consistency(path, ctx, outcome, scores, errors):
    if outcome not in ("WIN", "LOSS"):
        return
    games_valid = [g for g in scores if isinstance(g, list) and len(g) == 2
                   and all(isinstance(v, int) and not isinstance(v, bool) for v in g)]
    if not games_valid:
        return
    own_wins = sum(1 for g in games_valid if g[0] > g[1])
    opp_wins = sum(1 for g in games_valid if g[1] > g[0])
    if outcome == "WIN" and own_wins <= opp_wins:
        errors.add(
            f"{path}: {ctx} outcome=WIN but games won ({own_wins}) does not exceed "
            f"opponent's games won ({opp_wins}) in scores {scores}"
        )
    if outcome == "LOSS" and opp_wins <= own_wins:
        errors.add(
            f"{path}: {ctx} outcome=LOSS but opponent's games won ({opp_wins}) does not "
            f"exceed own games won ({own_wins}) in scores {scores}"
        )


def check_country(path, ctx, value, errors, allow_none=False):
    if value is None:
        if not allow_none:
            errors.add(f"{path}: {ctx} country is null")
        return
    if not isinstance(value, str) or not COUNTRY_RE.match(value):
        errors.add(f"{path}: {ctx} country {value!r} must be exactly 3 uppercase letters")


def check_entry(path, ctx, entry, rules, errors, is_results):
    for field in ENTRY_REQUIRED_COMMON:
        if field not in entry:
            errors.add(f"{path}: {ctx} entry missing '{field}'")
    check_country(path, ctx, entry.get("country"), errors)
    disc = entry.get("discipline")
    if disc not in rules["disciplines"]:
        errors.add(f"{path}: {ctx} discipline {disc!r} not in allowed set {rules['disciplines']}")
    athletes = entry.get("athletes")
    if not isinstance(athletes, list) or not athletes:
        errors.add(f"{path}: {ctx} athletes must be a non-empty list")
    elif disc in rules["maxAthletesByDiscipline"]:
        expected = rules["maxAthletesByDiscipline"][disc]
        if len(athletes) != expected:
            errors.add(
                f"{path}: {ctx} discipline {disc} expects {expected} athlete(s), "
                f"got {len(athletes)}"
            )
    if "seed" in entry and entry["seed"] is not None:
        if not isinstance(entry["seed"], int) or isinstance(entry["seed"], bool):
            errors.add(f"{path}: {ctx} seed must be an int or null")

    if is_results:
        matches = entry.get("matches")
        if not isinstance(matches, list):
            errors.add(f"{path}: {ctx} matches must be a list")
            return
        for mi, match in enumerate(matches):
            mctx = f"{ctx} match[{mi}]"
            for field in MATCH_REQUIRED:
                if field not in match:
                    errors.add(f"{path}: {mctx} missing '{field}'")
            round_ = match.get("round")
            if round_ not in rules["rounds"]:
                errors.add(f"{path}: {mctx} round {round_!r} not in allowed set {rules['rounds']}")
            outcome = match.get("outcome")
            if outcome not in rules["outcomes"]:
                errors.add(f"{path}: {mctx} outcome {outcome!r} not in allowed set {rules['outcomes']}")
            opp_country = match.get("opponentCountry")
            check_country(path, mctx, opp_country, errors, allow_none=True)
            opponent = match.get("opponent")
            if not isinstance(opponent, list):
                errors.add(f"{path}: {mctx} opponent must be a list")
            scores = match.get("scores")
            if rules.get("scoreFormat") == "games":
                check_scores(path, mctx, scores if isinstance(scores, list) else [], errors)
                if isinstance(scores, list):
                    check_win_loss_consistency(path, mctx, outcome, scores, errors)
            date_val = match.get("date")
            if date_val is not None:
                check_iso_date(path, f"{mctx}.date", date_val, errors, allow_none=True)


def validate_file(path, data, required_fields, rules_by_sport, errors, is_results):
    if data is None:
        return
    if data.get("schemaVersion") != 1:
        errors.add(f"{path}: schemaVersion must be 1, got {data.get('schemaVersion')!r}")
    if "sport" not in data:
        errors.add(f"{path}: top-level 'sport' field is required")
    if "updatedOn" in data:
        check_iso_date(path, "updatedOn", data.get("updatedOn"), errors)
    else:
        errors.add(f"{path}: missing 'updatedOn'")
    events = data.get("events")
    if not isinstance(events, list):
        errors.add(f"{path}: 'events' must be a list")
        return

    seen_ids = set()
    for ei, event in enumerate(events):
        ctx = f"event[{ei}]"
        name = event.get("name", "<unnamed>")
        ctx = f"event[{ei}] '{name}'"
        for field in required_fields:
            if field not in event:
                errors.add(f"{path}: {ctx} missing '{field}'")

        eid = event.get("id")
        if eid:
            if eid in seen_ids:
                errors.add(f"{path}: duplicate event id {eid!r}")
            seen_ids.add(eid)

        sport = event.get("sport")
        rules = rules_by_sport.get(sport)
        if rules is None:
            errors.add(
                f"{path}: {ctx} has unknown sport {sport!r}; add it to rules.json"
            )
            continue

        if event.get("ageCategory") not in rules["ageCategories"]:
            errors.add(f"{path}: {ctx} ageCategory {event.get('ageCategory')!r} not in {rules['ageCategories']}")
        if event.get("level") not in rules["levels"]:
            errors.add(f"{path}: {ctx} level {event.get('level')!r} not in {rules['levels']}")
        if is_results and event.get("status") not in rules["statuses"]:
            errors.add(f"{path}: {ctx} status {event.get('status')!r} not in {rules['statuses']}")

        start = check_iso_date(path, f"{ctx}.start", event.get("start"), errors)
        end = check_iso_date(path, f"{ctx}.end", event.get("end"), errors)
        if start is not None and end is not None and start > end:
            errors.add(f"{path}: {ctx} start ({start}) is after end ({end})")

        check_sources(path, ctx, event.get("sources"), errors)

        entries = event.get("entries")
        if not isinstance(entries, list):
            errors.add(f"{path}: {ctx} 'entries' must be a list")
            entries = []

        status = event.get("status")
        for eni, entry in enumerate(entries):
            ectx = f"{ctx} entries[{eni}]"
            check_entry(path, ectx, entry, rules, errors, is_results)
            if is_results and status == "FINISHED":
                for mi, match in enumerate(entry.get("matches", []) or []):
                    if match.get("outcome") == "SCHEDULED":
                        errors.add(
                            f"{path}: {ectx} match[{mi}] is SCHEDULED but event status is FINISHED"
                        )


def main():
    errors = Errors()

    rules_data = load_json("rules.json", errors)
    if rules_data is None:
        print("FAIL: could not load rules.json")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    upcoming = load_json("upcoming.json", errors)
    results = load_json("results.json", errors)

    validate_file("upcoming.json", upcoming, UPCOMING_EVENT_REQUIRED, rules_data, errors, is_results=False)
    validate_file("results.json", results, RESULTS_EVENT_REQUIRED, rules_data, errors, is_results=True)

    if errors:
        print(f"FAIL: {len(errors)} problem(s) found:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    print("OK: upcoming.json and results.json pass all checks.")
    sys.exit(0)


if __name__ == "__main__":
    main()
