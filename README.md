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

Optionally create `.env` from `.env.example` and set `IG_TARGET` to the
account you want to track, either the username (e.g. `neta.tuvian`) or the
display name (`neta tuvian`). Using the exact username is more reliable.

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

Options:

```bash
python track_follows.py --target neta.tuvian   # track a different account this run
python track_follows.py --headed               # show the browser window
```

## Files created

| File | Purpose |
|------|---------|
| `browser_profile/` | Chromium profile holding your Instagram login |
| `data/<username>/latest.json` | Most recent snapshot, used as the comparison baseline |
| `data/<username>/snapshot_<timestamp>.json` | History of every snapshot |
| `data/<username>/changes_<timestamp>.json` | Change report of every run after the first |

`.env`, `browser_profile/` and `data/` are git-ignored; they contain your
session and personal data.

## Notes

- The target account must be visible to your account (public, or private and
  you follow it).
- Instagram rate-limits aggressively. Do not run this more than a few times a
  day. Large accounts (tens of thousands of followers) take a while.
- If Instagram logs you out or asks you to confirm the login, delete
  `browser_profile/` and run again to log in fresh.
