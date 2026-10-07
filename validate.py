#!/usr/bin/env python3
"""Validate upcoming.json and results.json against schema v1 and rules.json.

Generic (sport-agnostic) checks live in this file: JSON parsing, schemaVersion,
required fields, ISO dates, start <= end, sources with label + url + readOn, no em
dash, no SCHEDULED matches in FINISHED events, country-code shape, and the
win/loss-vs-games-won consistency check.

Game-score law: every completed game of a decided match must be a legal score for the sport,
configured per sport in rules.json (`gameScoring`). Retired matches skip their unfinished last game.

Content checks: readOn and updatedOn may not be in the future (UTC today + 1 day); source
URLs must use a host in ALLOWED_SOURCE_HOSTS; a knockout loss must be an entry's last decided
match (check_progression says which formats are exempt).

Every results event carries `medals` (a real boolean; true only for Games and Championships, so
never on a TOUR or DEVELOPMENT event; a junior championship may have no level; parts of one
tournament must agree) and `timeZone` (a real IANA zone name, checked with zoneinfo; see
time_zone_problem for what happens without a tz database).

Player names (check_names): results.json may carry a top-level `names` directory, one object per
person with a supported sport, an IOC country, the `name` the app shows and the `aliases` the
sources also use. One spelling may name only one person per sport and country, compared the way
the app compares names (name_key). upcoming.json may not carry one: the app reads it from
results.json only.

Previous-version checks (see previous_version): the data files are compared with the version
before this change, taken from git. updatedOn may not go backwards, and results.json may not
lose an event id (upcoming.json drops finished events by design, so it is exempt). Which version
counts as "previous": the working-tree file differs from HEAD (the routine's local run before it
commits) -> HEAD; otherwise (CI, where the commit under test is HEAD) -> HEAD~1; with
`--previous-ref REF` -> REF, whatever HEAD says. Nothing to compare with -> a printed note, no check.

Rules the app's parsers (ResultsFeedParser.kt / UpcomingFeedParser.kt) enforce, which
this file mirrors because the app rejects the *whole* file on any one of them:
non-null required strings (reqString), a boolean entriesPublished (reqBoolean), ISO
dates and match times (LocalDate/LocalTime.parse), a `scores` array on every match and
rubber, no NOT_PLAYED team tie, no NaN/Infinity (org.json), an `order` on every rubber,
and rules.json values that stay inside the app's enums (APP_ENUMS).

Schema rules that are stricter than the parser on purpose (the app would accept the
data, but it is never legitimate): rubbers on a non-team match, string-typed booleans,
blank source labels/urls, non-IOC country codes, unknown sports.

Sport-specific vocabulary (disciplines, rounds, levels, ageCategories, outcomes,
statuses, score format, max athletes per discipline) lives in rules.json, keyed
by the event's own "sport" field. Adding a new sport means adding an entry to
rules.json, not editing this file. Adding a new country needs no change at all:
any IOC code (IOC_CODES) is accepted for `country` / `opponentCountry`.

Exits non-zero with a clear message on the first class of failures found (all
errors for a file are collected and reported together).
"""
import argparse
import json
import re
import subprocess
import sys
import unicodedata
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

EM_DASH = "—"
COUNTRY_RE = re.compile(r"^[A-Z]{3}$")
# ASCII-only and matched with fullmatch: `\d` accepts non-ASCII digits and `$` accepts a trailing
# newline, both of which the app's LocalDate.parse / LocalTime.parse reject.
ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The forms the app's LocalTime.parse (ISO_LOCAL_TIME) accepts: HH:mm, HH:mm:ss, HH:mm:ss.fffffffff
# (ResultsFeedParser.kt:156 parses every match `time`; a bad one rejects the whole file).
ISO_TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9](\.[0-9]{1,9})?)?")

# The app's own enums (score-orbit app/src/main/java/com/scoreorbit/app/data/FixtureModels.kt and
# ResultsModels.kt). The app parses these with Enum.valueOf, so a value outside them rejects the
# whole file. rules.json may narrow them per sport but must never add to them.
APP_ENUMS = {
    "disciplines": {"MS", "WS", "MD", "WD", "XD", "MT", "WT", "XT"},
    "levels": {"MAJOR", "TOUR", "DEVELOPMENT", None},
    "ageCategories": {"SENIOR", "JUNIOR"},
    "statuses": {"IN_PROGRESS", "FINISHED"},
    "outcomes": {
        "WIN", "LOSS", "WALKOVER_WIN", "WALKOVER_LOSS", "RETIRED_WIN", "RETIRED_LOSS",
        "BYE", "SCHEDULED", "NOT_PLAYED",
    },
    "rubberOutcomes": {
        "WIN", "LOSS", "WALKOVER_WIN", "WALKOVER_LOSS", "RETIRED_WIN", "RETIRED_LOSS",
        "BYE", "SCHEDULED", "NOT_PLAYED",
    },
}
# The app treats exactly these as team disciplines (Discipline.isTeam()) and applies its tie
# checks to them regardless of rules.json.
APP_TEAM_DISCIPLINES = {"MT", "WT", "XT"}

# Source URLs must point at one of these hosts (a listed domain or any subdomain of it, e.g.
# en.wikipedia.org for wikipedia.org). First block: hosts used by the data on 5 Oct 2026. Second
# block: official bodies and event sites we expect to cite. To cite a new site, add its domain here
# in the same commit that first uses it; an unlisted host fails validation on purpose, so a link
# to an unvetted site never reaches the feed unnoticed.
ALLOWED_SOURCE_HOSTS = [
    # in use today
    "wikipedia.org",
    "tribuneindia.com",
    "thebridge.in",
    "badmintonasia.org",
    # official / obvious
    "bwfbadminton.com",   # also bwfworldtour.bwfbadminton.com, bwfworldchampionships.bwfbadminton.com
    "ittf.com",
    "worldtabletennis.com",
    "olympics.com",
    "badmintoneurope.com",
    "tournamentsoftware.com",  # bwf.tournamentsoftware.com draws (may show a cookie wall)
    # national federations named in the app's docs/data-curation.md (entry lists and squads)
    "badmintonindia.org",
    "singaporebadminton.org.sg",
    "stta.org.sg",
    "ttfi.org",
    # reputable sports news that names squads and entry lists
    "thehindu.com",           # incl. sportstar.thehindu.com
    "indianexpress.com",
    "straitstimes.com",
]

# Rounds where a loss does not end an entry's run: group stage (the app's isGroupRound: G + digit).
GROUP_ROUND_RE = re.compile(r"G[0-9]")
QUALIFYING_ROUND_RE = re.compile(r"Q[0-9]+")
ELIMINATING_OUTCOMES = {"LOSS", "WALKOVER_LOSS", "RETIRED_LOSS"}  # the app's isElimination()

UPCOMING_EVENT_REQUIRED = [
    "id", "name", "shortName", "sport", "ageCategory", "level",
    "officialTierName", "location", "start", "end", "entriesPublished",
    "entries", "sources",
]
RESULTS_EVENT_REQUIRED = [
    "id", "name", "shortName", "sport", "ageCategory", "level",
    "officialTierName", "location", "medals", "timeZone", "start", "end", "status",
    "coverage", "entries", "sources",
]
ENTRY_REQUIRED_COMMON = ["country", "discipline", "athletes"]
MATCH_REQUIRED = [
    "round", "outcome", "opponent", "opponentCountry", "scores",
]
# `label` and `url` are reqString on every source (ResultsFeedParser.kt:177-178,
# UpcomingFeedParser.kt:80-81). `readOn` is optional for the app (never parsed as a date) but is
# required by this schema so every claim carries the day it was read.
SOURCE_REQUIRED = ["label", "url", "readOn"]
# Fields the app reads with reqString: present *and* non-null, or the whole file is rejected
# (ResultsFeedParser.kt:48-50; UpcomingFeedParser.kt:47-49, 54: upcoming `location` is required,
# results `location` is optional there). Tournament refs: UpcomingFeedParser.kt:104.
UPCOMING_EVENT_NON_NULL = ["id", "name", "shortName", "location"]
RESULTS_EVENT_NON_NULL = ["id", "name", "shortName"]
TOURNAMENT_NON_NULL = ["id", "name", "shortName"]


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
    def reject_constant(name):
        # Python's json accepts NaN/Infinity/-Infinity; Android's org.json refuses them
        # (JSONObject.put -> JSON.checkDouble throws JSONException), and the parsers turn any
        # JSONException into "reject the whole file" (ResultsFeedParser.kt:35-36,
        # UpcomingFeedParser.kt:32-33). No Kotlin line names this rule; the library enforces it.
        errors.add(f"{path}: contains {name}, which is not valid JSON and the app rejects it")
        return None

    try:
        return json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as e:
        errors.add(f"{path}: invalid JSON - {e}")
        return None


def check_iso_date(path, ctx, value, errors, allow_none=False):
    if value is None:
        if not allow_none:
            errors.add(f"{path}: {ctx} is null but a date is required")
        return None
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        errors.add(f"{path}: {ctx} = {value!r} is not an ISO date (YYYY-MM-DD)")
        return None
    try:
        y, m, d = (int(x) for x in value.split("-"))
        return date(y, m, d)
    except ValueError:
        errors.add(f"{path}: {ctx} = {value!r} is not a real calendar date")
        return None


def check_iso_time(path, ctx, value, errors):
    if not isinstance(value, str) or not ISO_TIME_RE.fullmatch(value):
        errors.add(f"{path}: {ctx} = {value!r} is not an ISO local time (HH:mm or HH:mm:ss)")


def check_non_null(path, ctx, obj, fields, errors):
    for field in fields:
        if field in obj and obj[field] is None:
            errors.add(f"{path}: {ctx} '{field}' is null but the app requires a value")


def latest_allowed_date():
    """Newest date readOn/updatedOn may carry: today (UTC) plus one day for time zones ahead of UTC."""
    return datetime.now(timezone.utc).date() + timedelta(days=1)


def check_not_future(path, ctx, value, errors):
    if value is not None and value > latest_allowed_date():
        errors.add(f"{path}: {ctx} = {value.isoformat()} is in the future (latest allowed is {latest_allowed_date().isoformat()})")


# A well-formed "Area/Location" zone name (America/Argentina/Buenos_Aires, America/Port-au-Prince,
# Etc/GMT+5), used only when this machine has no time zone database to check names against.
TIME_ZONE_SHAPE_RE = re.compile(r"[A-Za-z]+(/[A-Za-z0-9][A-Za-z0-9_+\-]*)+")
_known_time_zones = None


def known_time_zones():
    """The IANA zone names this machine knows (cached); empty when it has no time zone database."""
    global _known_time_zones
    if _known_time_zones is None:
        _known_time_zones = frozenset(zoneinfo.available_timezones())
    return _known_time_zones


def time_zone_problem(value):
    """(why `value` is not an acceptable `timeZone`, or None; True when only its shape was checked).

    A name is valid when it is in zoneinfo.available_timezones(). With no tz database on the machine
    that set is empty, so a well-formed "Area/Location" name is accepted instead (shape_only) and the
    caller prints a note rather than failing."""
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string naming an IANA time zone, e.g. 'Asia/Shanghai'", False
    known = known_time_zones()
    if known:
        if value in known:
            return None, False
        return "is not an IANA time zone name, e.g. 'Asia/Shanghai'", False
    if TIME_ZONE_SHAPE_RE.fullmatch(value):
        return None, True
    return "is not a well-formed IANA time zone name like 'Area/Location'", False


def source_url_problem(url):
    """Why `url` is not an acceptable source link, or None when it is."""
    if not isinstance(url, str):
        return "is not a string"
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "is not a valid URL"
    if parts.scheme not in ("http", "https") or not host:
        return "is not an http(s) URL with a host"
    host = host.lower().rstrip(".")
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_SOURCE_HOSTS):
        return f"has host {host!r}, which is not in ALLOWED_SOURCE_HOSTS (validate.py)"
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
            read_on = check_iso_date(path, f"{ctx} source[{i}].readOn", src["readOn"], errors)
            check_not_future(path, f"{ctx} source[{i}].readOn", read_on, errors)
        if src.get("url"):
            problem = source_url_problem(src["url"])
            if problem:
                errors.add(f"{path}: {ctx} source[{i}] url {src['url']!r} {problem}")


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


def game_scoring_config_problem(law):
    """Why a rules.json `gameScoring` block is unusable, or None."""
    if not isinstance(law, dict):
        return "must be an object {pointsToWin, winBy, cap}"
    points, win_by, cap = law.get("pointsToWin"), law.get("winBy"), law.get("cap")
    for name, value in (("pointsToWin", points), ("winBy", win_by)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return f"{name} must be an integer >= 1, got {value!r}"
    if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap <= points):
        return f"cap must be null or an integer above pointsToWin, got {cap!r}"
    return None


def game_law_problem(a, b, law):
    """Why a *completed* game ending a-b breaks the sport's scoring law, or None when it is legal.

    Law: first to `pointsToWin`, but only with a lead of `winBy`; at 'pointsToWin' all the lead can be
    short of `winBy`, so play continues until someone leads by `winBy`, or reaches `cap` (when set),
    where a one-point lead is enough (badminton 30-29)."""
    points, win_by, cap = law["pointsToWin"], law["winBy"], law.get("cap")
    win, lose = max(a, b), min(a, b)
    if win == lose:
        return "a completed game cannot be tied"
    if win < points:
        return f"the winner has {win}, fewer than the {points} needed"
    if cap is not None and win > cap:
        return f"the winner has {win}, above the cap of {cap}"
    if cap is not None and win == cap:
        if lose < cap - win_by:
            return f"a game only reaches the cap of {cap} when the loser has at least {cap - win_by}"
    elif win == points:
        if lose > points - win_by:
            return f"at {points} the winner must lead by {win_by}, so play continues"
    elif lose != win - win_by:
        return f"past {points} the winner leads by exactly {win_by}"
    return None


def check_game_law(path, ctx, outcome, scores, rules, errors):
    """Every completed game of a decided match must be a legal score for the sport.

    Retired matches (RETIRED_WIN/LOSS) end mid-game, so their last game is unfinished and skipped;
    earlier games are complete. Walkovers, byes and scheduled matches carry no completed games."""
    law = rules.get("gameScoring")
    if law is None or game_scoring_config_problem(law) or not isinstance(scores, list):
        return
    if outcome in ("WIN", "LOSS"):
        games = scores
    elif outcome in ("RETIRED_WIN", "RETIRED_LOSS"):
        games = scores[:-1]
    else:
        return
    for gi, game in enumerate(games):
        if (not isinstance(game, list) or len(game) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in game)):
            continue  # a malformed pair is reported by check_scores
        problem = game_law_problem(game[0], game[1], law)
        if problem:
            errors.add(f"{path}: {ctx} game {gi + 1} score {game[0]}-{game[1]} is not a legal game: {problem}")


def check_progression(path, ctx, matches, errors):
    """A knockout loss is the end of an entry's run: no decided match may follow it.

    Group rounds (G1-G5) are skipped (a group loss does not end the run). Two real formats do continue
    after a knockout loss and are allowed: a semi-final loser playing the 3P match, and a qualifying
    loser entering the main draw as a lucky loser. SCHEDULED matches are not decided and are ignored.
    The app reads the same data with ResultsEntry.isOut(), which looks at the last non-SCHEDULED
    match, so an entry that 'loses and keeps playing' would show inconsistently there."""
    for i, match in enumerate(matches):
        if not isinstance(match, dict) or match.get("outcome") not in ELIMINATING_OUTCOMES:
            continue
        rnd = match.get("round")
        if not isinstance(rnd, str) or GROUP_ROUND_RE.fullmatch(rnd):
            continue
        for j in range(i + 1, len(matches)):
            later = matches[j]
            if not isinstance(later, dict) or later.get("outcome") == "SCHEDULED":
                continue
            nxt = later.get("round")
            if rnd == "SF" and nxt == "3P":
                continue
            if (QUALIFYING_ROUND_RE.fullmatch(rnd) and isinstance(nxt, str)
                    and not QUALIFYING_ROUND_RE.fullmatch(nxt)):
                continue
            errors.add(
                f"{path}: {ctx} match[{j}] {nxt} ({later.get('outcome')}) comes after a knockout loss in "
                f"match[{i}] {rnd} ({match.get('outcome')}); a knockout loss ends the entry's run"
            )
            return


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


def name_key(raw):
    """A name as the app compares it (normalizeNameKey in the app's PlayerIndex.kt): accents and
    punctuation dropped, lower case, single spaces. "P.V. Sindhu" and "P. V. Sindhu" are one key,
    and so are "T. Sharma" and "T SHARMA"."""
    decomposed = unicodedata.normalize("NFD", raw)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).split())


def check_names(path, names, rules_by_sport, errors):
    """results.json's optional `names` directory: one object per person,
    {"sport", "country", "name", "aliases"}. `name` is the spelling the app shows; `aliases` are other
    spellings of the same person seen in a source. Inside one person the aliases are distinct strings
    and none is the name itself ("T. Sharma" and "T SHARMA" may both be listed). Across people of one
    sport and country the app matches by name_key, so the checks between people use it too: no two
    people with the same name, no alias listed for two people, no alias equal to another person's name."""
    if not isinstance(names, list):
        errors.add(f"{path}: names must be a list of people, got {type(names).__name__}")
        return
    owners = {}  # (sport, country, name_key) -> (index, name, "name" or "alias")
    for i, person in enumerate(names):
        ctx = f"names[{i}]"
        if not isinstance(person, dict):
            errors.add(f"{path}: {ctx} must be an object with sport, country, name and aliases")
            continue
        sport, country, name = person.get("sport"), person.get("country"), person.get("name")
        if sport not in rules_by_sport:
            errors.add(f"{path}: {ctx} sport {sport!r} is not a supported sport ({', '.join(sorted(rules_by_sport))})")
        country_errors = len(errors)
        check_country(path, ctx, country, errors)
        country_ok = len(errors) == country_errors
        if not isinstance(name, str) or not name_key(name):
            errors.add(f"{path}: {ctx} name must be a non-empty string, got {name!r}")
            name = None
        else:
            ctx = f"names[{i}] {name!r}"
        aliases = person.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            errors.add(f"{path}: {ctx} aliases must be a non-empty list of other spellings, got {aliases!r}")
            aliases = []
        spellings = [] if name is None else [(name, "name")]
        listed = set()
        for alias in aliases:
            if not isinstance(alias, str) or not name_key(alias):
                errors.add(f"{path}: {ctx} alias {alias!r} must be a non-empty string")
            elif alias in listed:
                errors.add(f"{path}: {ctx} alias {alias!r} is listed twice")
            elif alias == name:
                errors.add(f"{path}: {ctx} alias {alias!r} is the person's name; list only other spellings")
            else:
                listed.add(alias)
                spellings.append((alias, "alias"))
        if sport not in rules_by_sport or not country_ok:
            continue
        for spelling, role in spellings:
            key = (sport, country, name_key(spelling))
            if key not in owners:
                owners[key] = (i, spelling, role)
                continue
            other_index, other_spelling, other_role = owners[key]
            if other_index == i:
                continue  # one person's own spellings may share a key
            if role == "name" and other_role == "name":
                problem = f"is a second entry for {sport} {country} {other_spelling!r} (names[{other_index}]); one person, one entry"
            else:
                problem = (f"{role} {spelling!r} is also the {other_role} {other_spelling!r} of names[{other_index}] "
                           f"({sport} {country}); one spelling can name only one person")
            errors.add(f"{path}: {ctx} {problem}")


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
    if not isinstance(r_scores, list):
        errors.add(f"{path}: {ctx} scores must be a list (use [] when there is no score), got {r_scores!r}")
    if r_outcome == "NOT_PLAYED":
        if r_scores not in ([], None):
            errors.add(f"{path}: {ctx} NOT_PLAYED rubber must have no scores, got {r_scores!r}")
    else:
        scores_list = r_scores if isinstance(r_scores, list) else []
        check_scores(path, ctx, scores_list, errors)
        if r_outcome in ("WIN", "LOSS"):
            check_win_loss_consistency(path, ctx, r_outcome, scores_list, errors)
        check_game_law(path, ctx, r_outcome, scores_list, rules, errors)
    if r_outcome in ("WIN", "RETIRED_WIN", "WALKOVER_WIN"):
        return "WON"
    if r_outcome in ("LOSS", "RETIRED_LOSS", "WALKOVER_LOSS"):
        return "LOST"
    return None


def check_tie(path, ctx, match, rules, errors):
    outcome = match.get("outcome")
    if outcome == "NOT_PLAYED":
        # ResultsFeedParser.kt:137-140
        errors.add(f"{path}: {ctx} a tie can't be NOT_PLAYED (that outcome only exists on rubbers)")
    scores = match.get("scores")
    if not isinstance(scores, list):
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
            if not isinstance(scores, list):
                errors.add(f"{path}: {mctx} scores must be a list (use [] when there is no score)")
            score_format = rules.get("scoreFormatByDiscipline", {}).get(disc, rules.get("scoreFormat"))
            if score_format == "ties":
                check_tie(path, mctx, match, rules, errors)
            elif score_format == "games":
                check_scores(path, mctx, scores if isinstance(scores, list) else [], errors)
                if isinstance(scores, list):
                    check_win_loss_consistency(path, mctx, outcome, scores, errors)
                    check_game_law(path, mctx, outcome, scores, rules, errors)
            if score_format != "ties":
                # The parser reads `rubbers` on every match with getJSONArray (ResultsFeedParser.kt:143-144),
                # so a non-array value (even a falsy {} or "") rejects the whole file. A non-empty array on a
                # non-team match is accepted by the app but is never legitimate (team-schema.md), so it is
                # rejected here as a schema rule.
                rubbers = match.get("rubbers")
                if rubbers is not None and not isinstance(rubbers, list):
                    errors.add(f"{path}: {mctx} rubbers must be a list when present, got {rubbers!r}")
                elif rubbers:
                    errors.add(f"{path}: {mctx} rubbers only belong on a team tie, not a {disc} match")
            date_val = match.get("date")
            if date_val is not None:
                check_iso_date(path, f"{mctx}.date", date_val, errors, allow_none=True)
            time_val = match.get("time")
            if time_val is not None:
                check_iso_time(path, f"{mctx}.time", time_val, errors)
        check_progression(path, ctx, matches, errors)


TOURNAMENT_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def check_tournaments(path, events, errors):
    """Optional `tournament` {id, name, shortName} links parts of one tournament (e.g. Asian Games
    team and individual events). The app shows parts with the same sport and id as one
    tournament, so those parts must agree on the tournament's names, age category and id prefix.
    The same id may be used by several sports (each sport's parts combine separately). In
    results.json the parts must also agree on `medals`: a tournament either awards medals or not."""
    parts = {}
    medals_of = {}
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
        medals = event.get("medals")
        if isinstance(medals, bool):
            first_id, first_medals = medals_of.setdefault(key, (event.get("id"), medals))
            if medals != first_medals:
                errors.add(
                    f"{path}: {ctx} {t['id']!r} disagrees with another {key[0]} part on medals: "
                    f"{event.get('id')!r} has medals={str(medals).lower()} but {first_id!r} has "
                    f"medals={str(first_medals).lower()}"
                )


def check_tournament(path, ctx, event, errors):
    if event.get("tournament") is None:
        return
    tournament = event["tournament"]
    if not isinstance(tournament, dict):
        errors.add(f"{path}: {ctx} tournament must be an object or null")
        return
    for field in TOURNAMENT_NON_NULL:
        if tournament.get(field) is None:
            errors.add(f"{path}: {ctx} tournament missing '{field}'")


def check_rules_against_app(rules_by_sport, errors):
    for sport, rules in rules_by_sport.items():
        if "gameScoring" in rules:
            problem = game_scoring_config_problem(rules["gameScoring"])
            if problem:
                errors.add(f"rules.json: {sport} gameScoring {problem}")
        for key, allowed in APP_ENUMS.items():
            extra = [v for v in rules.get(key, []) if v not in allowed]
            if extra:
                errors.add(f"rules.json: {sport} {key} {extra!r} are not values the app knows")
        team = set(rules.get("teamDisciplines", []))
        expected_team = APP_TEAM_DISCIPLINES & set(rules.get("disciplines", []))
        if team != expected_team:
            errors.add(
                f"rules.json: {sport} teamDisciplines {sorted(team)} must be {sorted(expected_team)} "
                f"(the app's team disciplines)"
            )
        # The app applies its tie checks (ResultsFeedParser.kt:137-142, 148) to team disciplines
        # and only to them, so the validator must pick 'ties' for exactly those disciplines.
        for disc in rules.get("disciplines", []):
            fmt = rules.get("scoreFormatByDiscipline", {}).get(disc, rules.get("scoreFormat"))
            if disc in expected_team and fmt != "ties":
                errors.add(f"rules.json: {sport} {disc} scoreFormat must be 'ties', got {fmt!r}")
            elif disc not in expected_team and fmt != "games":
                errors.add(f"rules.json: {sport} {disc} scoreFormat must be 'games', got {fmt!r}")


def check_medals_and_time_zone(path, ctx, event, errors, shape_only_zones):
    """results.json only: `medals` is a real boolean and `timeZone` a real zone.

    Only Games and Championships award medals, so medals may not be true on a TOUR or DEVELOPMENT
    event. MAJOR is the usual level; null is allowed because a junior championship (World Juniors)
    often has no level (data-curation.md leaves a junior level null unless certain)."""
    if "medals" in event:
        medals = event["medals"]
        if not isinstance(medals, bool):
            errors.add(f"{path}: {ctx} medals must be true or false, got {medals!r}")
        elif medals and event.get("level") in ("TOUR", "DEVELOPMENT"):
            errors.add(f"{path}: {ctx} medals is true but level is {event.get('level')!r}; "
                       f"only Games and Championships award medals, never a TOUR or DEVELOPMENT event")
    if "timeZone" in event:
        problem, shape_only = time_zone_problem(event["timeZone"])
        if problem:
            errors.add(f"{path}: {ctx} timeZone {event['timeZone']!r} {problem}")
        elif shape_only:
            shape_only_zones.append(ctx)


def validate_file(path, data, required_fields, non_null_fields, rules_by_sport, errors, is_results, notes=None):
    if data is None:
        return
    if isinstance(data.get("schemaVersion"), bool) or data.get("schemaVersion") != 1:
        errors.add(f"{path}: schemaVersion must be 1, got {data.get('schemaVersion')!r}")
    if "sport" not in data:
        errors.add(f"{path}: top-level 'sport' field is required")
    if "updatedOn" in data:
        updated_on = check_iso_date(path, "updatedOn", data.get("updatedOn"), errors)
        check_not_future(path, "updatedOn", updated_on, errors)
    else:
        errors.add(f"{path}: missing 'updatedOn'")
    if "names" in data:
        if is_results:
            check_names(path, data["names"], rules_by_sport, errors)
        else:
            errors.add(f"{path}: 'names' belongs in results.json; the app reads the names directory from there only")
    events = data.get("events")
    if not isinstance(events, list):
        errors.add(f"{path}: 'events' must be a list")
        return
    check_tournaments(path, events, errors)

    seen_ids = set()
    shape_only_zones = []
    for ei, event in enumerate(events):
        ctx = f"event[{ei}]"
        name = event.get("name") or "<unnamed>"
        ctx = f"event[{ei}] '{name}'"
        for field in required_fields:
            if field not in event:
                errors.add(f"{path}: {ctx} missing '{field}'")
        check_non_null(path, ctx, event, non_null_fields, errors)
        # UpcomingFeedParser.kt:57 (reqBoolean). org.json would also accept the strings "true"/"false";
        # the schema wants a real JSON boolean.
        if not is_results and "entriesPublished" in event and not isinstance(event["entriesPublished"], bool):
            errors.add(f"{path}: {ctx} entriesPublished must be true or false, got {event['entriesPublished']!r}")
        if is_results:
            check_medals_and_time_zone(path, ctx, event, errors, shape_only_zones)
        check_tournament(path, ctx, event, errors)

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
    if shape_only_zones and notes is not None:
        notes.append(f"{path}: this machine has no time zone database, so {len(shape_only_zones)} timeZone "
                     f"value(s) were checked for their Area/Location shape only")


def git_show(ref, path):
    """Bytes of `path` at git `ref`, or None when git, the ref or the file is not available."""
    try:
        done = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def git_ref_exists(ref):
    try:
        done = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                              capture_output=True)
    except OSError:
        return False
    return done.returncode == 0


def previous_version(path, previous_ref):
    """(bytes of the previous version of `path` or None, short description for messages)."""
    if previous_ref:
        raw = git_show(previous_ref, path)
        if raw is None:
            return None, f"{previous_ref} has no {path}"
        return raw, previous_ref
    try:
        with open(path, "rb") as f:
            working = f.read()
    except OSError:
        working = None
    head = git_show("HEAD", path)
    if head is not None and working is not None and head != working:
        return head, "HEAD (working tree has uncommitted changes)"
    parent = git_show("HEAD~1", path)
    if parent is None:
        return None, "neither HEAD nor HEAD~1 has it: new file, first commit or shallow clone"
    return parent, "HEAD~1"


def quiet_iso_date(value):
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        return None
    try:
        return date(*(int(x) for x in value.split("-")))
    except ValueError:
        return None


def check_against_previous(path, current, is_results, previous_ref, errors, notes):
    """updatedOn never goes backwards; results.json never loses an event id."""
    if not isinstance(current, dict):
        return
    raw, source = previous_version(path, previous_ref)
    if raw is None:
        notes.append(f"{path}: previous-version checks skipped ({source})")
        return
    try:
        previous = json.loads(raw.decode("utf-8"))
        if not isinstance(previous, dict):
            raise ValueError("not an object")
    except ValueError:  # includes JSONDecodeError and UnicodeDecodeError
        notes.append(f"{path}: previous-version checks skipped (the {source} copy is not valid JSON)")
        return

    old_date, new_date = quiet_iso_date(previous.get("updatedOn")), quiet_iso_date(current.get("updatedOn"))
    if old_date is None:
        notes.append(f"{path}: updatedOn check skipped (the {source} copy has no valid updatedOn)")
    elif new_date is not None and new_date < old_date:
        changed = {k: v for k, v in current.items() if k != "updatedOn"} != \
                  {k: v for k, v in previous.items() if k != "updatedOn"}
        errors.add(
            f"{path}: updatedOn {new_date.isoformat()} is earlier than {old_date.isoformat()} in the "
            f"previous version ({source}); updatedOn may be kept or raised, never lowered"
            + (" (the content changed too)" if changed else " (only updatedOn changed)")
        )

    if is_results:
        def event_ids(data):
            events = data.get("events")
            return [e.get("id") for e in events if isinstance(e, dict)] if isinstance(events, list) else []
        now = set(event_ids(current))
        for eid in event_ids(previous):
            if eid not in now:
                errors.add(f"{path}: event {eid!r} is in the previous version ({source}) but missing now; "
                           f"results are never removed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate upcoming.json and results.json.")
    parser.add_argument("--previous-ref", metavar="REF",
                        help="compare with this git ref's version of each file instead of HEAD / HEAD~1")
    args = parser.parse_args(argv)
    previous_ref = args.previous_ref
    if previous_ref is not None and (previous_ref.startswith("-") or not git_ref_exists(previous_ref)):
        # An explicit ref that cannot be used must not silently turn the comparison off.
        print(f"FAIL: --previous-ref {previous_ref!r} does not resolve to a commit here")
        sys.exit(1)

    errors = Errors()
    notes = []

    rules_data = load_json("rules.json", errors)
    if rules_data is None:
        print("FAIL: could not load rules.json")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    upcoming = load_json("upcoming.json", errors)
    results = load_json("results.json", errors)

    check_rules_against_app(rules_data, errors)
    validate_file("upcoming.json", upcoming, UPCOMING_EVENT_REQUIRED, UPCOMING_EVENT_NON_NULL,
                  rules_data, errors, is_results=False)
    validate_file("results.json", results, RESULTS_EVENT_REQUIRED, RESULTS_EVENT_NON_NULL,
                  rules_data, errors, is_results=True, notes=notes)
    check_against_previous("upcoming.json", upcoming, False, previous_ref, errors, notes)
    check_against_previous("results.json", results, True, previous_ref, errors, notes)

    for note in notes:
        print("note:", note)
    if errors:
        print(f"FAIL: {len(errors)} problem(s) found:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    print("OK: upcoming.json and results.json pass all checks.")
    sys.exit(0)


if __name__ == "__main__":
    main()
