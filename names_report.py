#!/usr/bin/env python3
"""List player-name spellings that may be the same person, for the `names` directory in results.json.

Usage, from the repo root:  python3 names_report.py [APP_FIXTURES_DIR]

Reads results.json and upcoming.json next to this script and, when given, an app checkout's
`fixtures/` directory (its results.json, upcoming.json, draw CSVs and metadata.json opponents).
Per sport and country it lists:
- short forms: a name with an initial and a whole word ("T. Sharma", "A. BOBBA", "Gadde R.") with
  the fuller spellings in the data it could stand for (same rule as the app's NameResolution.kt);
- near-duplicates: spellings with the same words in another order ("Kidambi Srikanth" and
  "Srikanth Kidambi"), the same letters spaced differently ("Li Shi Feng" and "Li Shifeng"), or a
  hyphen where the other has a space ("Chi Yu Jen" and "Chi Yu-jen"; the app already joins these,
  and a `name` pins which form it shows).
A spelling already in the directory (a `name` or an alias) is not listed, and neither are two
spellings that differ only in case, accents or dots ("P.V. Sindhu" and "P. V. Sindhu"). A listed
pair is only a candidate: record it in `names` only when a source shows both spellings are the same
person. Standard library only; never changes a file.
"""
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UNDOTTED_INITIALS = re.compile(r"[A-Z]{1,3}")
ALL_CAPS_TOKEN = re.compile(r"[A-Z]+(?:['-][A-Z]+)*")
COUNTRY_SUFFIX = re.compile(r"(.*\S)\s*\(([A-Z]{3})\)")


def name_key(raw):
    """The app's normalizeNameKey (same as validate.name_key): no accents or punctuation, lower case."""
    decomposed = unicodedata.normalize("NFD", raw)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).split())


def hyphen_key(raw):
    """name_key, but a hyphen stays a hyphen: "Chi Yu-jen" and "Chi Yu Jen" differ here, not there."""
    decomposed = unicodedata.normalize("NFD", raw)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    spaced = " ".join(re.sub(r"[^a-z0-9-]+", " ", without_marks.lower()).split())
    return re.sub(r" ?- ?", "-", spaced)


def display_form(raw):
    """The app's formatSourceAthleteName: an all-caps name becomes "T. Sharma"; anything else stays."""
    letters = [c for c in raw if c.isalpha()]
    if not letters or any(c.islower() for c in letters):
        return raw

    def token(t):
        if not t or "." in t or not ALL_CAPS_TOKEN.fullmatch(t):
            return t
        if sum(c.isalpha() for c in t) <= 1:
            return t + "."
        return "-".join("'".join(s[:1] + s[1:].lower() for s in part.split("'")) for part in t.split("-"))

    return " ".join(token(t) for t in raw.split(" "))


def shape(raw):
    """(words, initials) of a name, as the app's nameShape reads its display form: a one-letter part
    is an initial ("T.", "M.R.", the "K" of "Pratheek.K"), so are up to three capitals in a mixed-case
    name ("PV"); a hyphen splits a word ("Se-young"); every other part is a whole word."""
    display = display_form(raw)
    mixed_case = any(c.islower() for c in display)
    tokens = []

    def add(part):
        key = name_key(part).replace(" ", "")
        if key:
            tokens.append(("i", key) if len(key) == 1 else ("w", key))

    for raw_part in display.split():
        if "." in raw_part:
            for piece in raw_part.split("."):
                for part in piece.split("-"):
                    add(part)
        elif mixed_case and UNDOTTED_INITIALS.fullmatch(raw_part):
            tokens.extend(("i", c.lower()) for c in raw_part)
        else:
            for part in raw_part.split("-"):
                add(part)
    return tokens


def words(tokens):
    return [t for kind, t in tokens if kind == "w"]


def initials(tokens):
    return [t for kind, t in tokens if kind == "i"]


def is_short(tokens):
    return bool(words(tokens)) and bool(initials(tokens))


def same_person_keys(tokens):
    """Keys under which two spellings are one person: the words in any order (initials in order), or,
    for a name without initials, all its letters run together."""
    keys = []
    if words(tokens):
        keys.append("order:" + " ".join(sorted(words(tokens))) + "|" + "".join(initials(tokens)))
        if not initials(tokens):
            keys.append("spacing:" + "".join(words(tokens)))
    return keys


def expands(fuller, shorter):
    """The app's NameShape.expands: every word of `shorter` is in `fuller`, and its initials, in order,
    are the first letters of `fuller`'s other parts, with something fuller than an initial."""
    if not is_short(shorter):
        return False
    rest = list(fuller)
    for word in words(shorter):
        at = next((n for n, t in enumerate(rest) if t == ("w", word)), None)
        if at is None:
            return False
        del rest[at]
    letters = initials(shorter)
    if len(letters) > len(rest) or any(rest[n][1][0] != letters[n] for n in range(len(letters))):
        return False
    return len(rest) > len(letters) or any(rest[n][0] == "w" for n in range(len(letters)))


class Names:
    """Every spelling seen, per (sport, country), with the files it was seen in."""

    def __init__(self):
        self.seen = defaultdict(lambda: defaultdict(lambda: {"spellings": set(), "where": set()}))

    def add(self, sport, country, raw, where):
        if not isinstance(sport, str) or not isinstance(country, str) or not isinstance(raw, str):
            return
        key = name_key(raw)
        if key and re.fullmatch(r"[A-Z]{3}", country.strip().upper()):
            slot = self.seen[(sport, country.strip().upper())][key]
            slot["spellings"].add(raw.strip())
            slot["where"].add(where)

    def add_feed(self, data, where):
        """A results.json or upcoming.json: entries, opponents and team rubbers."""
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            sport = event.get("sport")
            for entry in event.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                own = entry.get("country")
                for athlete in entry.get("athletes") or []:
                    self.add(sport, own, athlete, where)
                for match in entry.get("matches") or []:
                    if not isinstance(match, dict):
                        continue
                    other = match.get("opponentCountry")
                    for opponent in match.get("opponent") or []:
                        self.add(sport, other, opponent, where)
                    for rubber in match.get("rubbers") or []:
                        if isinstance(rubber, dict):
                            for athlete in rubber.get("athletes") or []:
                                self.add(sport, own, athlete, where)
                            for opponent in rubber.get("opponent") or []:
                                self.add(sport, other, opponent, where)

    def add_fixtures(self, directory):
        """An app checkout's fixtures/: its feed copies, the draw CSVs metadata.json names (with that
        event's sport), and the opponents in metadata.json's progression rows."""
        for name in ("results.json", "upcoming.json"):
            path = directory / name
            if path.is_file():
                self.add_feed(json.loads(path.read_text(encoding="utf-8")), f"fixtures/{name}")
        path = directory / "metadata.json"
        if not path.is_file():
            return
        for event in json.loads(path.read_text(encoding="utf-8")):
            sport = event.get("sport")
            for csv_name in [event.get("csv")] + list(event.get("additionalCsv") or []):
                if csv_name and (directory / csv_name).is_file():
                    with (directory / csv_name).open(encoding="utf-8", newline="") as f:
                        for row in csv.DictReader(f):
                            for n in (1, 2):
                                country = row.get(f"country_{n}") or row.get("entry_country")
                                self.add(sport, country, row.get(f"athlete_{n}"), f"fixtures/{csv_name}")
            for rows in (event.get("progression") or {}).values():
                for row in rows:
                    opponent, country = row.get("opponent"), row.get("opponentCountry")
                    if not isinstance(opponent, str):
                        continue
                    suffix = COUNTRY_SUFFIX.fullmatch(opponent.strip())
                    if suffix:
                        opponent, country = suffix.group(1), country or suffix.group(2)
                    for part in opponent.split(" / "):
                        self.add(sport, country, part, "fixtures/metadata.json")


def directory_keys(results):
    """(sport, country) -> the name_keys of every `name` and alias in results.json's `names`."""
    covered = defaultdict(set)
    people = results.get("names") if isinstance(results.get("names"), list) else []
    for person in people:
        if not isinstance(person, dict) or not isinstance(person.get("country"), str):
            continue
        slot = covered[(person.get("sport"), person["country"])]
        aliases = person.get("aliases") if isinstance(person.get("aliases"), list) else []
        for spelling in [person.get("name")] + aliases:
            if isinstance(spelling, str):
                slot.add(name_key(spelling))
    return covered


def shown(slot):
    """One spelling of a key to print (see first_spelling)."""
    return first_spelling(slot["spellings"])


def first_spelling(spellings):
    """The spelling with the fewest capitals ("T. Sharma" over "T SHARMA"), then alphabetical."""
    return min(spellings, key=lambda s: (sum(c.isupper() for c in s), s))


def candidates(names, covered):
    """(sport, country) -> lines to print, and how many short forms have no fuller spelling in the data."""
    report = {}
    unmatched = 0
    for group in sorted(names.seen):
        keys = names.seen[group]
        done = covered.get(group, set())
        shapes = {key: [shape(s) for s in slot["spellings"]] for key, slot in keys.items()}
        lines = []
        # Near-duplicates: one person under several keys (words reordered or respaced).
        by_same = defaultdict(set)
        for key, key_shapes in shapes.items():
            for tokens in key_shapes:
                for same in same_person_keys(tokens):
                    by_same[same].add(key)
        reported = set()
        for same, members in sorted(by_same.items()):
            members = frozenset(members)
            if len(members) < 2 or members in reported or members <= done:
                continue
            reported.add(members)
            kind = "order" if same.startswith("order:") else "spacing"
            lines.append((kind, " ~ ".join(f'"{shown(keys[k])}"' for k in sorted(members, key=lambda k: shown(keys[k])))))
        # Near-duplicates within one key: a hyphen where another spelling has a space.
        for key in sorted(keys, key=lambda k: shown(keys[k])):
            forms = defaultdict(set)
            for spelling in keys[key]["spellings"]:
                forms[hyphen_key(spelling)].add(spelling)
            if len(forms) > 1 and key not in done:
                lines.append(("hyphen", " ~ ".join(sorted(f'"{first_spelling(f)}"' for f in forms.values()))))
        # Short forms with the fuller spellings that expand them (the ones nothing fuller expands again).
        for key in sorted(keys, key=lambda k: shown(keys[k])):
            if key in done or not any(is_short(t) for t in shapes[key]):
                continue
            fuller = {other for other in keys if other != key and any(
                expands(f, s) for f in shapes[other] for s in shapes[key])}
            fullest = sorted((c for c in fuller if not any(
                expands(f, s) for d in fuller if d != c for f in shapes[d] for s in shapes[c])), key=lambda k: shown(keys[k]))
            if not fullest:
                unmatched += 1
                continue
            where = ", ".join(sorted(keys[key]["where"]))
            targets = ", ".join(f'"{shown(keys[c])}"' for c in fullest)
            lines.append(("short", f'"{shown(keys[key])}" -> {targets}  ({where})'))
        if lines:
            report[group] = lines
    return report, unmatched


def main(argv):
    if len(argv) > 2 or (len(argv) == 2 and argv[1].startswith("-")):
        print("usage: python3 names_report.py [APP_FIXTURES_DIR]")
        return 2
    names = Names()
    try:
        results = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
        upcoming = json.loads((ROOT / "upcoming.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"names_report: cannot read results.json or upcoming.json: {e}")
        return 1
    names.add_feed(results, "results.json")
    names.add_feed(upcoming, "upcoming.json")
    read = "results.json, upcoming.json"
    if len(argv) == 2:
        fixtures = Path(argv[1])
        if fixtures.is_dir():
            try:
                names.add_fixtures(fixtures)
                read += f", {fixtures}"
            except (OSError, ValueError) as e:
                print(f"note: skipped {fixtures}: {e}")
        else:
            print(f"note: {fixtures} is not a directory; reporting on the data files only")
    covered = directory_keys(results)
    report, unmatched = candidates(names, covered)
    count = sum(len(lines) for lines in report.values())
    print(f"names_report: {count} candidate(s) not in the names directory (read {read})")
    for (sport, country), lines in report.items():
        print(f"{sport} {country}")
        for kind, text in lines:
            print(f"  {kind:<7} {text}")
    if unmatched:
        print(f"({unmatched} short form(s) have no fuller spelling in the data and are not listed)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
