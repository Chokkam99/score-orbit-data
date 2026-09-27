# ScoreOrbit data

Public data files for the ScoreOrbit app: upcoming and recently finished international
badminton and table tennis events with Indian and Singaporean entries.

- `upcoming.json`: schema version 1. Each event lists its sources and the date they were read.
- `results.json`: schema version 1. Finished-event match results (round, outcome, opponent,
  scores) for the same countries, with sources and read dates.
- `rules.json`: per-sport vocabulary (allowed disciplines, rounds, levels, age categories,
  outcomes, statuses, score format) used by `validate.py`. To add a country, nothing to
  change. To add a sport, add its rules to `rules.json`. Supported sports: `BADMINTON`, `TABLE_TENNIS`
  (the app skips events for any other sport). Table tennis ids use `wtt-results:`/`wtt-upcoming:`.
- Facts come from public sources (tournament calendars, federation announcements, sports news,
  tournament results pages). Nothing here is guessed; unconfirmed entry lists are left empty,
  and a match is only recorded when a source shows it.
- Run `python3 validate.py` before committing.
- A GitHub Actions workflow (`.github/workflows/validate.yml`) runs `python3 validate.py` on every push and pull request.
- Updated weekly by a reviewed pull request.

## Team events

A team entry (`MT`/`WT`/`XT`) has empty `athletes` and a `matches` list of ties, each with a tie
score (`scores: [[own, opponent]]`) and, optionally, its `rubbers` (each an individual-discipline
game within that tie, own points first).

Rubbers are never individual entries: a country's singles/doubles rubber inside a team event stays
nested under its tie, and never gets its own top-level entry alongside the team entry.
