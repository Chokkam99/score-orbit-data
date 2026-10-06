# ScoreOrbit data

Public data files for the ScoreOrbit app: upcoming and recently finished international
badminton and table tennis events with Indian and Singaporean entries.

The Android app downloads `results.json` and `upcoming.json` straight from
`https://raw.githubusercontent.com/Chokkam99/score-orbit-data/main/`. Whatever is on `main` is what
phones read, after raw.githubusercontent.com's short cache (a few minutes).

## Files

- `upcoming.json`: schema version 1. Each event lists its sources and the date they were read.
- `results.json`: schema version 1. Finished-event match results (round, outcome, opponent,
  scores) for the same countries, with sources and read dates. Every event also carries `medals`
  and `timeZone` (see "Results event fields").
- `rules.json`: per-sport vocabulary (allowed disciplines, rounds, levels, age categories,
  outcomes, statuses, score format, game scoring law) used by `validate.py`. To add a country,
  nothing to change. To add a sport, add its rules to `rules.json`. Supported sports: `BADMINTON`,
  `TABLE_TENNIS` (the app skips events for any other sport). Table tennis ids use
  `wtt-results:`/`wtt-upcoming:`.
- `validate.py`: the validator described below. `tests/test_validate.py`: its self-tests.
- `heartbeat.py`: the staleness check run by the heartbeat workflow.
- `.github/workflows/promote.yml`, `validate.yml`, `heartbeat.yml`: the gate to `main`, the
  validation run on every push, and the daily staleness check.
- `variants.json`: not read by the app, `validate.py` or CI. It was committed with the rubber-order
  fix (2580568) and its purpose is not documented.
- `LICENSE`: which licence covers what (data CC BY-SA 4.0, code MIT).

Facts come from public sources (tournament calendars, federation announcements, sports news,
tournament results pages), mostly Wikipedia. Nothing here is guessed; unconfirmed entry lists are
left empty, and a match is only recorded when a source shows it.

## How the data is updated

A scheduled routine runs every day at 00:30 UTC. It reads the public pages, edits `results.json` and
`upcoming.json`, sets `results.json`'s `updatedOn` to the run date (even when nothing else changed,
so the date says when the data was last checked), runs `python3 validate.py`, and when that prints
`OK` it commits and pushes to the `staging` branch, never to `main`.

`main` is what the app downloads. The `promote.yml` workflow moves `main` forward to `staging` only
when staging builds on the current `main`, changes nothing but `results.json` and `upcoming.json`, and
passes `python3 validate.py --previous-ref origin/main` plus the self-tests. Otherwise `main` stays
where it was, phones keep the last good data, and the failed run emails the repo owner. Because the
routine can only change the two data files, the validator, `rules.json` and the workflows that judge
its data are always `main`'s own.

The `validate.yml` workflow also runs `python3 validate.py` and the self-tests on every push and pull
request, as a second check.

To check by hand before committing: `python3 validate.py`. To run the self-tests:
`python3 -m unittest discover -s tests`. Python 3 standard library only.

## What validate.py checks

- Everything the app's parsers (`ResultsFeedParser.kt`, `UpcomingFeedParser.kt`) reject a whole
  file for: missing or null required strings, enum values, ISO dates and match times, a `scores`
  list on every match, NaN/Infinity, and so on. A rejected file leaves phones on their last good copy.
- Schema rules: sources with `label`, `url` and `readOn`, IOC country codes, athlete counts per
  discipline, tie scores that agree with their rubbers, tournament tags, `medals` and `timeZone` on
  every results event, no em dash.
- Game scores follow the sport's law (`gameScoring` in `rules.json`): badminton games go to 21, win by
  2, capped at 30 (30-29 is legal); table tennis games go to 11, win by 2, no cap. Every completed
  game of a WIN/LOSS match or rubber is checked, and the winner by games must match the outcome.
- `readOn` and `updatedOn` are not in the future (today UTC plus one day is allowed).
- Source URLs use a host in `ALLOWED_SOURCE_HOSTS` at the top of `validate.py` (a domain or any
  subdomain of it). Only the repo owner adds a host, in a commit to `main`; the routine may not change
  `validate.py` (the promote workflow refuses it), so it reports a source it could not use instead.
- A knockout loss is an entry's last decided match. Group rounds are exempt, a semi-final loser may
  play the `3P` match, and a qualifying loser may enter the main draw as a lucky loser.

### Previous-version checks

`validate.py` also compares the data with the version before the change, taken from git:

- `updatedOn` may be kept or raised, never lowered.
- `results.json` may not lose an event id that the previous version had. `upcoming.json` is exempt,
  because finished events leave it by design.

Which version counts as "previous":

- the working-tree file differs from `HEAD` (a local run before committing): `HEAD`;
- otherwise (CI, where the commit under test is `HEAD`): `HEAD~1`, which is why `validate.yml` checks
  out two commits;
- `python3 validate.py --previous-ref origin/main`: that ref's version of each file, whatever `HEAD`
  says. A ref that does not resolve fails the run; a ref that lacks the file skips that file.

If there is nothing to compare with (first commit, shallow clone, not a git repo) the check is
skipped and a `note:` line says so. CI sees only the last commit of a push, so when several commits
are pushed at once it compares the tip with its parent, not each commit.

## Heartbeat

`heartbeat.yml` runs daily at 06:00 UTC (and on demand) and runs `heartbeat.py`, which fails when the
newer `updatedOn` of `results.json` and `upcoming.json` is more than 2 days before today (UTC); the
routine bumps `results.json` on every run, so this means no run has published for three days. It exists
because nothing else notices a routine that has stopped. When a scheduled run fails, GitHub emails the
account that last edited the workflow's cron line (the repo owner), if that account has failed-workflow
email notifications on, which is the default.

## Retirements, walkovers and scheduled matches

- `RETIRED_WIN` / `RETIRED_LOSS`: `scores` holds the games played up to the retirement, so the last
  game is unfinished (for example `[[11, 21], [4, 13]]`). The game law is not applied to that last game.
- `WALKOVER_WIN` / `WALKOVER_LOSS`: `scores` is `[]`.
- `SCHEDULED`: `scores` is `[]`; only allowed while the event is `IN_PROGRESS`.

## Team events

A team entry (`MT`/`WT`/`XT`) has empty `athletes` and a `matches` list of ties, each with a tie
score (`scores: [[own, opponent]]`) and, optionally, its `rubbers` (each an individual-discipline
game within that tie, own points first).

Rubbers are never individual entries: a country's singles/doubles rubber inside a team event stays
nested under its tie, and never gets its own top-level entry alongside the team entry.

## Results event fields: medals and timeZone

Every event in `results.json` (not `upcoming.json`) has both fields, written right after `location`.
`medals` is a JSON boolean: `true` only for events that award medals, which are multi-sport Games
(Olympics, Asian Games, Commonwealth Games) and Championships (World Championships, continental
championships), and `false` for everything else, including MAJOR-level events that award prizes but
no medals (BWF World Tour Finals, WTT Grand Smashes such as China Smash, WTT Finals). Junior
championships (World Juniors, Asia Juniors) award medals too. `validate.py` refuses `medals: true`
on a `TOUR` or `DEVELOPMENT` event (`MAJOR` is usual; a junior championship may have no level), and
the parts of one tournament must agree on it.

`timeZone` is the IANA time zone of the venue, for example `Asia/Shanghai`; it lets the app show
match times in the fan's own time zone. `validate.py` accepts a name only when it is in Python's
`zoneinfo.available_timezones()`; on a machine with no time zone database it checks just the
`Area/Location` shape and prints a `note:` line instead of failing.

## Multi-event tournaments

When one tournament has separate events (the Asian Games team and individual events), each part
stays its own event, with its own dates, status and format, and carries the same
`tournament: {"id", "name", "shortName"}`, e.g. `{"id": "asian-games-2026", "name": "Asian Games
2026", "shortName": "Asian Games"}`. The app shows the parts as one tournament. Parts of one sport
must agree on the tournament names, age category, id prefix and (in `results.json`) `medals`;
`validate.py` checks this.

## Group stages and elimination

A loss in a group match (G1 to G5) doesn't end an entry's run in the app; the group isn't decided
by one result. When the source shows an entry is out although it has matches left (for example,
it can no longer qualify from its group), set `"eliminated": true` on that entry. A knockout loss,
a final or a third-place match ends the run without the flag.

## Licence

Data: CC BY-SA 4.0 (much of it derives from Wikipedia). Code: MIT. See `LICENSE`.
