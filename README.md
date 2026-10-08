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
  and `timeZone` (see "Results event fields"). An optional top-level `names` lists players whose
  name the sources spell more than one way (see "Player names").
- `rules.json`: per-sport vocabulary (allowed disciplines, rounds, levels, age categories,
  outcomes, statuses, score format, game scoring law) used by `validate.py`. To add a country,
  nothing to change. To add a sport, add its rules to `rules.json`. Supported sports: `BADMINTON`,
  `TABLE_TENNIS`, `SQUASH` (the app skips events for any sport it doesn't support yet). Table
  tennis ids use `wtt-results:`/`wtt-upcoming:`, squash ids `squash-results:`/`squash-upcoming:`.
  The file also holds rules for five sports that score one result per side instead of games:
  `BOXING`, `WRESTLING`, `ARCHERY`, `HOCKEY`, `KABADDI` (see "Score sports"). Their ids use
  `<sport>-results:` and `<sport>-upcoming:`, for example `boxing-results:world-championships-2025`.
- `validate.py`: the validator described below. `tests/test_validate.py`: its self-tests.
- `names_report.py`: lists spellings that may be the same person and are not in `names` yet (see
  "Player names"). `tests/test_names_report.py`: its self-tests.
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
  2, capped at 30 (30-29 is legal); table tennis and squash games go to 11, win by 2, no cap. Every
  completed game of a WIN/LOSS match or rubber is checked, and the winner by games must match the
  outcome. An event that plays a different law sets its own `gameScoring` object on the event,
  which replaces the sport's for that event only: the Squash World Cup plays games to 7 with
  sudden death at 6-6, `{"pointsToWin": 7, "winBy": 1, "cap": null}`.
- Squash has no third-place match, so `3P` is not a squash round. At the Asian Games both losing
  semifinalists win bronze.
- `readOn` and `updatedOn` are not in the future (today UTC plus one day is allowed).
- Source URLs use a host in `ALLOWED_SOURCE_HOSTS` at the top of `validate.py` (a domain or any
  subdomain of it). Only the repo owner adds a host, in a commit to `main`; the routine may not change
  `validate.py` (the promote workflow refuses it), so it reports a source it could not use instead.
- A knockout loss is an entry's last decided match. Group rounds (`G1` to `G16`) are exempt, a
  semi-final loser may play the `3P` match, and a qualifying loser may enter the main draw as a lucky
  loser. Wrestling may continue into repechage after an early loss, and hockey into classification
  matches after a quarter-final loss (see "Score sports").
- Score sports (boxing, wrestling, archery, hockey, kabaddi) have their own laws, read from their
  `rules.json` blocks: see "Score sports".
- The `names` directory in `results.json` follows the rules under "Player names"; `upcoming.json` may
  not have one.

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

## Score sports

Boxing, wrestling, archery, hockey and kabaddi record one score per side for each match, not games.
Their blocks in `rules.json` say `"scoreFormat": "score"`. Racket sports keep `ties` for `MT`, `WT`
and `XT`; in a score sport every event, team events included, is a `score` match. The validator
accepts these sports now. Data for them is published only once the app build that reads them is out,
because older builds skip an event of a sport they don't know.

Only `SENIOR` events are accepted for now (junior events are out of scope), and a sport's `levels` list
is exactly the levels it uses. Each block also says in `thirdPlaceMatch` whether the sport plays a
bronze match. Wrestling, archery and hockey do, so they list `3P`. Boxing and kabaddi don't, so
`3P` is not one of their rounds: both semi-final losers win bronze there.

### Events

A score sport lists its events in `rules.json` as an object, one key per event code:

```json
"M60": {"name": "Men's 60 kg", "gender": "M", "kind": "SINGLE"}
```

- `name` is the name the app shows. There is one name per code, so two sources cannot spell one event
  two ways ("Men's 60 kg", never "Men's 60kg"). Use plain ASCII apostrophes.
- `gender` is `M`, `W` or `X` (mixed).
- `kind` says who is on the entry and how many athletes it lists:
  - `SINGLE`: 1.
  - `PAIR`: 2.
  - `CREW`: 2 to 6 athletes of one country. A discipline may pin the exact number with `athletes`:
    archery teams list 3 and mixed teams 2.
  - `TEAM`: none. A national team whose players are not listed (hockey and kabaddi).
- Archery events also carry `scoring` (`recurve` or `compound`) and, for compound, `maxTotal`.

An entry writes `discipline` (the code) and repeats `disciplineName`, `gender` and `kind`; the
validator checks all three against `rules.json` and rejects a code the sport doesn't list. Hockey and
kabaddi write the bare racket codes `MT` and `WT`, as racket sports do, and need none of the three
(the app ignores them for a racket code). A code may contain `+` (`M+90`).

| Sport | Codes |
| --- | --- |
| Boxing | Men `M50`, `M55`, `M60`, `M65`, `M70`, `M75`, `M80`, `M85`, `M90`, `M+90`. Women `W48`, `W51`, `W54`, `W57`, `W60`, `W65`, `W70`, `W75`, `W80`, `W+80` |
| Wrestling | Style plus weight. Men's freestyle `FS57`, `FS61`, `FS65`, `FS70`, `FS74`, `FS79`, `FS86`, `FS92`, `FS97`, `FS125`. Men's Greco-Roman `GR55`, `GR60`, `GR63`, `GR67`, `GR72`, `GR77`, `GR82`, `GR87`, `GR97`, `GR130`. Women's `WW50`, `WW53`, `WW55`, `WW57`, `WW59`, `WW62`, `WW65`, `WW68`, `WW72`, `WW76` |
| Archery | Individual `RM`, `RW` (recurve), `CM`, `CW` (compound). Teams of 3 `RMT`, `RWT`, `CMT`, `CWT`. Mixed teams of 2 `RXT`, `CXT` |
| Hockey, kabaddi | `MT`, `WT` |

Codes and laws that could not be confirmed from the data probes (Wikipedia only):

- Weight classes are those of World Boxing's 2025 events and UWW's 2025 and 2026 events. Not read from
  any page: the 2026 Commonwealth Games boxing weights, the 2028 Olympic boxing and wrestling weights,
  and whether UWW changed a weight after 2026. An event with a weight outside these lists fails
  validation until the code is added to `rules.json`.
- World Boxing Cup stage I of 2025 used 5 kg bands ("47-50 kg"). They have no code here.
- The archery codes are this project's own scheme. The ten events themselves are the ones the World
  Cup, the Worlds and the 2026 Asian Games list.
- Recurve final pairs 7-1 (individual) and 6-2 and 5-4 (team) come from the set-point rule, not from a
  page. The compound maxima of 150 (individual) and 240 (team) come from the arrow counts; the highest
  totals seen were 149 and 238. A mixed team's 160 was seen.
- Wrestling `DSQ` (disqualification) is in the method list so that a win from behind by
  disqualification can be recorded. No disqualified bout was seen in the probes, so its code and its
  law are unconfirmed.
- A kabaddi knockout tie-break was not seen on any page. The field exists in case one is shown.

### A match

A score match keeps the usual fields (`round`, `outcome`, `opponent`, `opponentCountry`, `scores`,
`date`, `time`) and may add the fields below. Leave one of them out when the page doesn't show it; never
write `null` for `method`, `methodRound`, `methodTime` or `tiebreak`.

| Field | Meaning |
| --- | --- |
| `scores` | One pair `[own, opponent]`, or `[]`. Judges' votes (boxing), technical points (wrestling), set points or the match total (archery), goals (hockey), points (kabaddi). Empty for a walkover, a bye, a scheduled match and any bout decided without a score. |
| `method` | How it was decided, a code from the sport's `methods` in `rules.json`. Recorded only when the page states it. |
| `methodRound` | Boxing only: the round a bout was stopped, 1 to 3. |
| `methodTime` | Wrestling only: the time of a fall or technical superiority, written `m:ss` (`2:31`, `0:04`). |
| `tiebreak` | `[own, opponent]`: hockey's shootout, archery's shoot-off, a kabaddi tie-break. See below. |

`outcome` may also be `DRAW`. It is allowed only in hockey and kabaddi, only in a group round
(`G1` to `G16`), with a level pair in `scores` and no `tiebreak`. A DRAW is never an elimination.

The validator checks, for every score sport:

- `scores` holds one pair of non-negative whole numbers, or none.
- A WIN or LOSS with a pair has the winner strictly ahead. The outcome, not the score, says who won.
  The exceptions are listed per sport below.
- A `tiebreak` goes only with a level score, and the side that wins it is the side the outcome names.
- `method` is in the sport's list, goes with the outcome, and agrees with the score (a method can
  require a pair or forbid one).
- A team entry (`TEAM`) has no athletes and no named opponent. Rubbers don't exist in a score sport.

### Per sport

- **Boxing.** Rounds `R64`, `R32`, `R16`, `QF`, `SF`, `F` ("Preliminaries" is the round before the next
  named one: twice its size). There is no `3P`: both semi-final losers win bronze.
  - `scores` are the judges' votes: each side 0 to 5, the total 3 to 5, the winner strictly ahead.
    They apply to any pair, whether or not `method` is written.
  - Methods: `DEC` (judges' decision, needs the votes); `RSC`, `RSC-I` and `KO` (a stoppage: no
    scores, optional `methodRound`); `DSQ` (no scores); `ABD` (abandon, outcome `RETIRED_WIN` or
    `RETIRED_LOSS`, no scores). A walkover is `WALKOVER_WIN` or `WALKOVER_LOSS` with no method.
  - Levels `MAJOR` (World Championships, Olympics, Asian Games, Commonwealth Games, Asian
    Championships) and `TOUR` (World Boxing Cup, only where a page shows bouts).
- **Wrestling.** Rounds `R32`, `R16`, `QF`, `SF`, `F`, repechage `REP1` and `REP2`, and the bronze
  bout `3P`.
  - `scores` are technical points. The winner is at least level. A win from behind is accepted only
    with `FALL` or `DSQ`. Margins (how far ahead a technical superiority must be) are not checked,
    because real pages break them.
  - Methods: `FALL` and `TECH_SUP` (both may carry `methodTime`), `POINTS`, `INJURY` (outcome
    `RETIRED_WIN` or `RETIRED_LOSS`), `DSQ`. Wikipedia rarely says technical superiority or a points
    win, and a margin is never used to guess either: leave `method` out.
  - A loss in `R32`, `R16` or `QF` may be followed by `REP1`, `REP2` and `3P`; a semi-final loss by
    `3P`. A loss in a repechage or bronze bout ends the run. Set `"eliminated": true` once the source
    shows the opponent did not reach the final.
- **Archery.** Rounds `R128`, `R96`, `R64`, `R48`, `R32`, `R24`, `R16`, `QF`, `SF`, `F`, `3P`. The
  ranking round is not recorded; put its rank in the entry's `seed` when the page shows it.
  - Recurve `scores` are set points. Individual (first to 6): 6-0, 6-2, 6-4, 7-1, 7-3 or 6-5. Team and
    mixed team (first to 5): 6-0, 5-1, 6-2, 5-3 or 5-4. The 6-5 and 5-4 come from a shoot-off, so
    they may carry a `tiebreak`; no other pair may.
  - Compound `scores` are the match totals: at most 150 individual, 240 team, 160 mixed team. A level
    total needs a `tiebreak`. An archery `tiebreak` may itself be level (`[10, 10]`, closest to the
    centre decides); the outcome says who won.
  - A team entry lists its 3 archers (mixed team 2) in `athletes`. `opponent` names the opposing
    archers when the page shows them, else `[]`. There are no methods.
  - Levels `MAJOR`, `TOUR` and `DEVELOPMENT` (Asia Cup).
- **Hockey.** Events `MT` and `WT`. Rounds `G1` to `G16` (pool and league matches), classification
  matches `5P`, `7P`, `9P`, `11P`, `13P`, `15P`, and `QF`, `SF`, `3P`, `F`.
  - `scores` are goals, any whole numbers. A level score is a `DRAW` in a group round, or carries the
    shootout in `tiebreak` and is a WIN or LOSS (Singapore v Bangladesh, women's 9th place: 1-1,
    shootout 1-2, a `LOSS` for Singapore). A cancelled match is left out, with a line in `notes`.
  - A classification match may follow a quarter-final or semi-final loss. A loss in a classification
    match or the bronze match ends the run.
  - Levels `MAJOR`, `TOUR` (Pro League, Asian Champions Trophy) and `DEVELOPMENT` (Nations Cup).
- **Kabaddi.** Events `MT` and `WT`. Rounds `G1` to `G16`, `QF`, `SF`, `F`. There is no `3P`: both
  semi-final losers win bronze. `scores` are points. A level score is a `DRAW` in a group round, or
  carries a `tiebreak` in a knockout. Level `MAJOR` only. The rival World Kabaddi "World Cup" is not
  recorded, because the IKF, the OCA and the AKF don't recognise it.

Country codes stay IOC only. Wikipedia sometimes writes ISO-style codes (`IRN` for Iran, `IRE` for
Ireland, `JAP` for Japan, `MYS` for Malaysia); map each to its IOC code (`IRI`, `IRL`, `JPN`, `MAS`),
and never guess one you can't map.

### Out of scope: medallist-only cups

Some events are covered on Wikipedia by medallists only, with no bouts or matches: World Boxing Cup
stages and Finals, and archery World Cup stages and the Final. Nothing is recorded for them yet. A
match is never built from a medal list. The placing format (slice S4) is meant to carry "won gold"
for such events without a match.

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

A loss in a group match (G1 to G5 in racket sports, G1 to G16 in hockey and kabaddi) doesn't end an
entry's run in the app; the group isn't decided by one result. When the source shows an entry is out although it has matches left (for example,
it can no longer qualify from its group), set `"eliminated": true` on that entry. A knockout loss,
a final or a third-place match ends the run without the flag.

## Player names

The sources spell some players more than one way ("Tanvi Sharma", "T. Sharma", "T SHARMA"). The
optional top-level `names` array in `results.json` says which spellings are one person:

```json
"names": [
  {"sport": "BADMINTON", "country": "IND", "name": "Tanvi Sharma", "aliases": ["T. Sharma", "T SHARMA"]}
]
```

`name` is the spelling the app shows: the player's full name as their federation profile writes it.
`aliases` are other spellings of the same person seen in any source. Event entries keep the source's
own spelling; nothing rewrites them. Record an alias only when a source shows both spellings are the
same person (the same profile link or player id, or a page naming both forms), never because two
names look alike. The first two entries are the people the app's code also maps (P. V. Sindhu,
Satwiksairaj Rankireddy); they keep the spelling the app already showed.

`validate.py` checks that `names`, when present, is a list of objects, each with a supported sport,
an IOC country code, a non-empty `name` and a non-empty list of distinct non-empty `aliases`, none of
them the `name` itself. Within one sport and country, no two entries have the same `name`, no alias
is listed for two people, and no alias is another person's `name`. Those comparisons ignore case,
accents and punctuation, the way the app compares names, so "T. Sharma" and "T SHARMA" count as one
spelling there (one person may still list both).

The app reads the directory first: a spelling listed here shows as its `name` on every screen, and a
`name` is final, never resolved to a longer one. Spellings not listed fall back to the app's own
rules, which join an abbreviation to the one full name in the data that fits it. A malformed `names`
section, or one bad entry in it, is skipped by the app; it never costs the file or an event.

`python3 names_report.py [APP_FIXTURES_DIR]` lists spellings that may need an entry: short forms
(initials, all-caps surnames) with the fuller spellings in the data they could stand for, and
near-duplicates (the same words in another order, the same letters spaced differently, or a hyphen
where another spelling has a space), grouped by sport and country. It reads `results.json`, `upcoming.json` and, when given, an app checkout's
`fixtures/` (its feed copies, draw CSVs and `metadata.json` opponents), and skips spellings already
in `names`. Its candidates are leads to check against a source, not facts.

## Licence

Data: CC BY-SA 4.0 (much of it derives from Wikipedia). Code: MIT. See `LICENSE`.
