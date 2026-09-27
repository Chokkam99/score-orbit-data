# ScoreOrbit data

Public data files for the ScoreOrbit app: upcoming and recently finished international
badminton events with Indian and Singaporean entries.

- `upcoming.json`: schema version 1. Each event lists its sources and the date they were read.
- `results.json`: schema version 1. Finished-event match results (round, outcome, opponent,
  scores) for the same countries, with sources and read dates.
- `rules.json`: per-sport vocabulary (allowed disciplines, rounds, levels, age categories,
  outcomes, statuses, score format) used by `validate.py`. To add a country, nothing to
  change. To add a sport, add its rules to `rules.json`.
- Facts come from public sources (tournament calendars, federation announcements, sports news,
  tournament results pages). Nothing here is guessed; unconfirmed entry lists are left empty,
  and a match is only recorded when a source shows it.
- Run `python3 validate.py` before committing.
- Updated weekly by a reviewed pull request.
