#!/usr/bin/env python3
"""Fail when the data has stopped being updated.

Exits 1 when results.json or upcoming.json has an `updatedOn` more than MAX_AGE_DAYS days before
today (UTC), or none that parses. Run daily by .github/workflows/heartbeat.yml; standard library only.
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
    for name in FILES:
        try:
            with open(f"{base}/{name}", encoding="utf-8") as f:
                updated_on = json.load(f).get("updatedOn")
            if not isinstance(updated_on, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", updated_on):
                raise ValueError(f"updatedOn is {updated_on!r}, not a YYYY-MM-DD date")
            age = (today - date.fromisoformat(updated_on)).days
        except (OSError, ValueError, AttributeError) as e:  # includes JSONDecodeError
            problems.append(f"{name}: cannot read updatedOn ({e})")
            continue
        if age > MAX_AGE_DAYS:
            problems.append(f"{name}: updatedOn {updated_on} is {age} days before {today.isoformat()} "
                            f"(limit {MAX_AGE_DAYS}); the daily routine has stopped publishing")
        else:
            print(f"{name}: updatedOn {updated_on}, {age} day(s) before {today.isoformat()}")
    return problems


def main():
    problems = check(datetime.now(timezone.utc).date())
    for p in problems:
        print("STALE:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
