# instegram_scraper

Tracks who an Instagram user follows and who follows them, and shows what
changed between runs (new / lost followers, new follows / unfollows).

It drives a real Chromium browser with Playwright. You log in to Instagram
yourself in that browser the first time (password, Facebook login or 2FA all
work), and the login is remembered in a local browser profile.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Create `.env` from `.env.example` and set `IG_TARGET` to the username of the
account you want to track:

```bash
cp .env.example .env
# edit .env -> IG_TARGET=neta_tuvian
```

## Run

```bash
python track_follows.py
```

- **First run** opens a browser window. Log in to Instagram there. Once the
  script sees you are logged in it finds the target account, downloads the
  followers and following lists and saves a baseline in `data/<username>/`.
- **Every later run** reuses the saved login (browser stays hidden), downloads
  the lists again, prints a change report (new followers, lost followers,
  started following, unfollowed, username changes) and saves both the new
  snapshot and the change report.

### Why the numbers are lower than the profile shows

Instagram never returns the complete followers / following list in one pass.
Each run typically gets around 80 % of it, and the part that is missing
changes from run to run. (A few percent on top of that are deactivated or
restricted accounts that Instagram counts but never lists.)

To keep the change report accurate anyway, the script does two things:

- Anyone who was in the previous snapshot but did not show up in this run's
  fetch is checked individually through the list's own search box. People
  confirmed still present are kept in the snapshot, so the saved list fills
  in toward the true total over a few runs.
- That search misses a few percent of people who are actually there, so a
  person is reported as lost / unfollowed only after being missing on two
  consecutive runs. In between they are listed under "possibly lost" (and
  logged in `changes.csv` as `possibly_lost_follower` / `possibly_unfollowed`).

The first run after an incomplete baseline will therefore report a burst of
"new" people that are really just ones the baseline missed. From the third
run on, the report is reliable.

Options:

```bash
python track_follows.py --thorough             # get the complete lists (slow, see below)
python track_follows.py --target other_user   # track a different account this run
python track_follows.py --headed               # show the browser window
```

### Getting the complete lists: `--thorough`

The normal fetch returns only part of each list (see below). `--thorough`
additionally sweeps the list's search box for every username prefix
(`a`, `b`, ... `9`, `.`, `_`), drilling into longer prefixes when a page is
full, and unions everything it finds. Each run adds up to 240 searches, so it
takes two or three thorough runs on different days to reach (or get within a
handful of deactivated accounts of) the counts shown on the profile.

Each thorough run is capped at 120 searches per list and stops early if
Instagram starts refusing requests, keeping everything found so far. The
sweep starts from a different letter each time, so if one run is cut short
the next `--thorough` run fills in the rest. Ctrl-C during the sweep or the
verification also keeps what was found and saves normally.

Use it for the first baseline and then occasionally, for example weekly.
Daily runs can stay without it: the verification step keeps the snapshot
accurate for people already known.

## Files created

| File | Purpose |
|------|---------|
| `browser_profile/` | Chromium profile holding your Instagram login |
| `data/<username>/latest.json` | Most recent snapshot, used as the comparison baseline |
| `data/<username>/snapshot_<timestamp>.csv` | Full followers + following list of that run (one file per run) |
| `data/<username>/history.csv` | One row per run: totals, profile counts, and how many changed |
| `data/<username>/changes.csv` | Running log of every individual change across all runs |
| `data/<username>/snapshot_<timestamp>.json` | Same snapshot as JSON |
| `data/<username>/changes_<timestamp>.json` | Change report of that run as JSON |

`history.csv` columns: `taken_at, target, followers, following,
reported_followers, reported_following, new_followers, lost_followers,
started_following, unfollowed`.

`changes.csv` columns: `taken_at, change, user_id, username, full_name` where
`change` is one of `new_follower`, `lost_follower`, `started_following`,
`unfollowed`, `username_change`, `possibly_lost_follower`, `possibly_unfollowed`.

`.env`, `browser_profile/` and `data/` are git-ignored; they contain your
session and personal data.

## Notes

- The target account must be visible to your account (public, or private and
  you follow it).
- **Run it at most once a day.** Instagram locks accounts it thinks are
  automated. A plain run is a few hundred small requests; `--thorough` adds
  up to 240 more. Several runs in one day, or repeated `--thorough` runs,
  have led to a temporary account lock. If that happens, confirm your
  identity in the Instagram app and do not run the script for 48 hours.
- Consider using a separate Instagram account for tracking rather than your
  main one. It only needs to be able to see the target's followers list.
- If Instagram logs you out or asks you to confirm the login, delete
  `browser_profile/` and run again to log in fresh.
