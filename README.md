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

The script also reads the follower / following counts shown on the profile
and tells you if the downloaded lists are shorter. A small gap is normal:
Instagram counts deactivated and restricted accounts that it never lists.

Options:

```bash
python track_follows.py --target other_user   # track a different account this run
python track_follows.py --headed               # show the browser window
```

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
`unfollowed`, `username_change`.

`.env`, `browser_profile/` and `data/` are git-ignored; they contain your
session and personal data.

## Notes

- The target account must be visible to your account (public, or private and
  you follow it).
- Instagram rate-limits aggressively. Do not run this more than a few times a
  day. Large accounts (tens of thousands of followers) take a while.
- If Instagram logs you out or asks you to confirm the login, delete
  `browser_profile/` and run again to log in fresh.
