#!/usr/bin/env python3
"""Fail when the data has stopped being updated.

The daily routine sets results.json's `updatedOn` to the run date on every run, even when nothing
else changed, while upcoming.json changes about weekly. So the newer of the two dates is the date of
the last run. Exits 1 when that date is more than MAX_AGE_DAYS days before today (UTC), or when a
file's `updatedOn` doesn't parse. Run daily by .github/workflows/heartbeat.yml; standard library only.
"""
import json
import re
import sys
from datetime import date, datetime, timezone

MAX_AGE_DAYS = 2
FILES = ["results.json", "upcoming.json"]


def check(today, base="."):
    """Return a list of problem strings for the data files under `base` (empty when fresh)."""
    problems = []
    dates = {}
    for name in FILES:
        try:
            with open(f"{base}/{name}", encoding="utf-8") as f:
                updated_on = json.load(f).get("updatedOn")
            if not isinstance(updated_on, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", updated_on):
                raise ValueError(f"updatedOn is {updated_on!r}, not a YYYY-MM-DD date")
            dates[name] = date.fromisoformat(updated_on)
        except (OSError, ValueError, AttributeError) as e:  # includes JSONDecodeError
            problems.append(f"{name}: cannot read updatedOn ({e})")
    if dates:
        name, newest = max(dates.items(), key=lambda item: item[1])
        age = (today - newest).days
        if age > MAX_AGE_DAYS:
            problems.append(f"newest updatedOn is {newest.isoformat()} ({name}), {age} days before "
                            f"{today.isoformat()} (limit {MAX_AGE_DAYS}); the daily routine has stopped publishing")
        else:
            print(f"newest updatedOn {newest.isoformat()} ({name}), {age} day(s) before {today.isoformat()}")
    return problems


def main():
    problems = check(datetime.now(timezone.utc).date())
    for p in problems:
        print("STALE:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
