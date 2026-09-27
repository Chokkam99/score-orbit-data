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
any IOC code (IOC_CODES) is accepted for `country` / `opponentCountry`.

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


# IOC country codes (plus ENG/SCO/WAL and TPE), matching the app's flag table in
# score-orbit app/src/main/java/com/scoreorbit/app/data/Flags.kt. A code outside this set shows
# with no flag or name in the app (e.g. "IRN" instead of IOC "IRI" for Iran).
IOC_CODES = {
    "AFG", "ALB", "ALG", "AND", "ANG", "ANT", "ARG", "ARM", "ARU", "ASA", "AUS", "AUT", "AZE",
    "BAH", "BAN", "BAR", "BDI", "BEL", "BEN", "BER", "BHU", "BIH", "BIZ", "BOL", "BOT", "BRA",
    "BRN", "BRU", "BUL", "BUR", "CAF", "CAM", "CAN", "CAY", "CGO", "CHA", "CHI", "CHN", "CIV",
    "CMR", "COD", "COK", "COL", "COM", "CPV", "CRC", "CRO", "CUB", "CYP", "CZE", "DEN", "DJI",
    "DMA", "DOM", "ECU", "EGY", "ENG", "ERI", "ESA", "ESP", "EST", "ETH", "FIJ", "FIN", "FRA",
    "FSM", "GAB", "GAM", "GBR", "GBS", "GEO", "GEQ", "GER", "GHA", "GRE", "GRN", "GUA", "GUI",
    "GUM", "GUY", "HAI", "HKG", "HON", "HUN", "INA", "IND", "IRI", "IRL", "IRQ", "ISL", "ISR",
    "ISV", "ITA", "IVB", "JAM", "JOR", "JPN", "KAZ", "KEN", "KGZ", "KIR", "KOR", "KSA", "KUW",
    "LAO", "LAT", "LBA", "LBN", "LBR", "LCA", "LES", "LIE", "LTU", "LUX", "MAD", "MAS", "MAW",
    "MDA", "MDV", "MEX", "MGL", "MHL", "MKD", "MLI", "MLT", "MNE", "MON", "MOZ", "MRI", "MTN",
    "MYA", "NAM", "NCA", "NED", "NEP", "NGR", "NIG", "NOR", "NRU", "NZL", "OMA", "PAK", "PAN",
    "PAR", "PER", "PHI", "PLE", "PLW", "PNG", "POL", "POR", "PRK", "PUR", "QAT", "ROU", "RSA",
    "RUS", "RWA", "SAM", "SCO", "SEN", "SEY", "SGP", "SKN", "SLE", "SLO", "SMR", "SOL", "SOM",
    "SRB", "SRI", "SSD", "STP", "SUD", "SUI", "SUR", "SVK", "SWE", "SWZ", "SYR", "TAN", "TGA",
    "THA", "TJK", "TKM", "TLS", "TOG", "TPE", "TTO", "TUN", "TUR", "TUV", "UAE", "UGA", "UKR",
    "URU", "USA", "UZB", "VAN", "VEN", "VIE", "VIN", "WAL", "YEM", "ZAM", "ZIM",
}


def check_country(path, ctx, value, errors, allow_none=False):
    if value is None:
        if not allow_none:
            errors.add(f"{path}: {ctx} country is null")
        return
    if not isinstance(value, str) or not COUNTRY_RE.match(value):
        errors.add(f"{path}: {ctx} country {value!r} must be exactly 3 uppercase letters")
    elif value not in IOC_CODES:
        errors.add(f"{path}: {ctx} country {value!r} is not an IOC code (e.g. Iran is IRI, not IRN)")


def check_rubber(path, ctx, rubber, rules, errors):
    individual_disciplines = [
        d for d in rules["disciplines"] if d not in set(rules.get("teamDisciplines", []))
    ]
    if not isinstance(rubber, dict):
        errors.add(f"{path}: {ctx} is not an object")
        return None
    # The app requires an integer `order` on every rubber; a missing one rejects the whole file.
    order = rubber.get("order")
    if not isinstance(order, int) or isinstance(order, bool) or order < 1:
        errors.add(f"{path}: {ctx} order must be an integer >= 1 (the app requires it), got {order!r}")
    rdisc = rubber.get("discipline")
    if rdisc not in individual_disciplines:
        errors.add(
            f"{path}: {ctx} discipline {rdisc!r} not in individual disciplines {individual_disciplines}"
        )
    r_athletes = rubber.get("athletes")
    if not isinstance(r_athletes, list) or not r_athletes:
        errors.add(f"{path}: {ctx} athletes must be a non-empty list")
    elif rdisc in rules["maxAthletesByDiscipline"]:
        expected = rules["maxAthletesByDiscipline"][rdisc]
        if len(r_athletes) != expected:
            errors.add(
                f"{path}: {ctx} discipline {rdisc} expects {expected} athlete(s), "
                f"got {len(r_athletes)}"
            )
    r_opponent = rubber.get("opponent")
    if not isinstance(r_opponent, list) or not r_opponent:
        errors.add(f"{path}: {ctx} opponent must be a non-empty list")
    elif rdisc in rules["maxAthletesByDiscipline"]:
        expected = rules["maxAthletesByDiscipline"][rdisc]
        if len(r_opponent) != expected:
            errors.add(
                f"{path}: {ctx} opponent for discipline {rdisc} expects {expected} name(s), "
                f"got {len(r_opponent)}"
            )
    r_outcome = rubber.get("outcome")
    if r_outcome not in rules.get("rubberOutcomes", []):
        errors.add(
            f"{path}: {ctx} outcome {r_outcome!r} not in allowed set {rules.get('rubberOutcomes', [])}"
        )
    r_scores = rubber.get("scores")
    if r_outcome == "NOT_PLAYED":
        if r_scores not in ([], None):
            errors.add(f"{path}: {ctx} NOT_PLAYED rubber must have no scores, got {r_scores!r}")
    else:
        scores_list = r_scores if isinstance(r_scores, list) else []
        check_scores(path, ctx, scores_list, errors)
        if r_outcome in ("WIN", "LOSS"):
            check_win_loss_consistency(path, ctx, r_outcome, scores_list, errors)
    if r_outcome in ("WIN", "RETIRED_WIN", "WALKOVER_WIN"):
        return "WON"
    if r_outcome in ("LOSS", "RETIRED_LOSS", "WALKOVER_LOSS"):
        return "LOST"
    return None


def check_tie(path, ctx, match, rules, errors):
    outcome = match.get("outcome")
    scores = match.get("scores")
    if not isinstance(scores, list):
        errors.add(f"{path}: {ctx} scores must be a list")
        scores = []
    opponent = match.get("opponent")
    if isinstance(opponent, list) and opponent:
        errors.add(f"{path}: {ctx} opponent must be empty for a team tie (the opponent is a country)")

    zero_allowed = outcome in ("SCHEDULED", "WALKOVER_WIN", "WALKOVER_LOSS")
    if zero_allowed:
        if len(scores) not in (0, 1):
            errors.add(
                f"{path}: {ctx} tie scores must have 0 or 1 pair for outcome {outcome!r}, "
                f"got {len(scores)}"
            )
    else:
        if len(scores) != 1:
            errors.add(f"{path}: {ctx} tie scores must have exactly one pair, got {len(scores)}")

    tie_score = None
    if len(scores) == 1:
        pair = scores[0]
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in pair)):
            errors.add(f"{path}: {ctx} tie score {pair!r} must be a pair of non-negative ints")
        else:
            tie_score = tuple(pair)
            own, opp = tie_score
            if outcome == "WIN" and own <= opp:
                errors.add(
                    f"{path}: {ctx} outcome=WIN but tie score {tie_score} does not favor own side"
                )
            if outcome == "LOSS" and opp <= own:
                errors.add(
                    f"{path}: {ctx} outcome=LOSS but tie score {tie_score} does not favor opponent"
                )

    rubbers = match.get("rubbers")
    if rubbers is not None:
        if not isinstance(rubbers, list):
            errors.add(f"{path}: {ctx} rubbers must be a list")
            rubbers = []
        won = 0
        lost = 0
        for ri, rubber in enumerate(rubbers):
            rctx = f"{ctx} rubbers[{ri}]"
            result = check_rubber(path, rctx, rubber, rules, errors)
            if result == "WON":
                won += 1
            elif result == "LOST":
                lost += 1
        if rubbers and tie_score is not None and (won, lost) != tie_score:
            errors.add(
                f"{path}: {ctx} rubber tally (won={won}, lost={lost}) does not match tie score {tie_score}"
            )


def check_entry(path, ctx, entry, rules, errors, is_results, event_id=None):
    for field in ENTRY_REQUIRED_COMMON:
        if field not in entry:
            errors.add(f"{path}: {ctx} entry missing '{field}'")
    check_country(path, ctx, entry.get("country"), errors)
    if "eliminated" in entry and not isinstance(entry["eliminated"], bool):
        errors.add(f"{path}: {ctx} eliminated must be true or false when present, got {entry['eliminated']!r}")
    disc = entry.get("discipline")
    team_disciplines = set(rules.get("teamDisciplines", []))
    is_team = disc in team_disciplines
    if disc not in rules["disciplines"]:
        errors.add(f"{path}: {ctx} discipline {disc!r} not in allowed set {rules['disciplines']}")

    if event_id and "team" in event_id.lower() and disc in rules["disciplines"] and not is_team:
        errors.add(
            f"{path}: {ctx} discipline {disc!r} is an individual discipline but sits in "
            f"team event {event_id!r}; individual rubbers must not be recorded as entries"
        )

    athletes = entry.get("athletes")
    if is_team:
        if not isinstance(athletes, list) or athletes:
            errors.add(f"{path}: {ctx} team entry athletes must be an empty list")
    else:
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
            round_ = match.get("round")
            outcome = match.get("outcome")
            opp_country = match.get("opponentCountry")
            mctx = f"{ctx} match[{mi}] {round_} vs {opp_country}"
            for field in MATCH_REQUIRED:
                if field not in match:
                    errors.add(f"{path}: {mctx} missing '{field}'")
            if round_ not in rules["rounds"]:
                errors.add(f"{path}: {mctx} round {round_!r} not in allowed set {rules['rounds']}")
            if outcome not in rules["outcomes"]:
                errors.add(f"{path}: {mctx} outcome {outcome!r} not in allowed set {rules['outcomes']}")
            check_country(path, mctx, opp_country, errors, allow_none=True)
            opponent = match.get("opponent")
            if not isinstance(opponent, list):
                errors.add(f"{path}: {mctx} opponent must be a list")
            scores = match.get("scores")
            score_format = rules.get("scoreFormatByDiscipline", {}).get(disc, rules.get("scoreFormat"))
            if score_format == "ties":
                check_tie(path, mctx, match, rules, errors)
            elif score_format == "games":
                check_scores(path, mctx, scores if isinstance(scores, list) else [], errors)
                if isinstance(scores, list):
                    check_win_loss_consistency(path, mctx, outcome, scores, errors)
            date_val = match.get("date")
            if date_val is not None:
                check_iso_date(path, f"{mctx}.date", date_val, errors, allow_none=True)


TOURNAMENT_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def check_tournaments(path, events, errors):
    """Optional `tournament` {id, name, shortName} links parts of one tournament (e.g. Asian Games
    team and individual events). The app shows parts with the same sport and id as one
    tournament, so those parts must agree on the tournament's names, age category and id prefix.
    The same id may be used by several sports (each sport's parts combine separately)."""
    parts = {}
    for ei, event in enumerate(events):
        if not isinstance(event, dict) or event.get("tournament") is None:
            continue
        t = event["tournament"]
        ctx = f"event[{ei}] {event.get('name')!r} tournament"
        if not isinstance(t, dict) or not all(isinstance(t.get(k), str) and t.get(k) for k in ("id", "name", "shortName")):
            errors.add(f"{path}: {ctx} must be an object with non-empty id, name and shortName")
            continue
        if not TOURNAMENT_ID_RE.match(t["id"]):
            errors.add(f"{path}: {ctx} id {t['id']!r} must be a lowercase slug like 'asian-games-2026'")
        prefix = str(event.get("id", "")).split(":", 1)[0]
        key = (event.get("sport"), t["id"])
        signature = (t["name"], t["shortName"], event.get("ageCategory"), prefix)
        if key in parts and parts[key] != signature:
            errors.add(
                f"{path}: {ctx} {t['id']!r} disagrees with another {key[0]} part on name, shortName, "
                f"ageCategory or id prefix: {signature} vs {parts[key]}"
            )
        parts.setdefault(key, signature)


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
    check_tournaments(path, events, errors)

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
            ectx = f"{ctx} entries[{eni}] ({entry.get('country')} {entry.get('discipline')})"
            check_entry(path, ectx, entry, rules, errors, is_results, event_id=eid)
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
