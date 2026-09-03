# instegram_scraper

Tracks who an Instagram user follows and who follows them, and shows what
changed between runs (new / lost followers, new follows / unfollows).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in your Instagram username and password. Set `IG_TARGET`
to the account you want to track, either the username (e.g. `neta.tuvian`) or
the display name (`neta tuvian`). Using the exact username is more reliable.

## Run

```bash
python track_follows.py
```

- **First run** logs in (you may be asked for a 2FA / verification code),
  finds the target account, downloads the followers and following lists and
  saves a baseline in `data/<username>/`.
- **Every later run** downloads the lists again, prints a change report
  (new followers, lost followers, started following, unfollowed, username
  changes) and saves both the new snapshot and the change report.

Override the target for a single run:

```bash
python track_follows.py --target neta.tuvian
```

## Files created

| File | Purpose |
|------|---------|
| `session.json` | Saved Instagram login session, so you are not logged in every run |
| `data/<username>/latest.json` | Most recent snapshot, used as the comparison baseline |
| `data/<username>/snapshot_<timestamp>.json` | History of every snapshot |
| `data/<username>/changes_<timestamp>.json` | Change report of every run after the first |

`.env`, `session.json` and `data/` are git-ignored; they contain credentials
and personal data.

## Notes

- The target account must be visible to your account (public, or private and
  you follow it).
- Instagram rate-limits aggressively. Do not run this more than a few times a
  day, and expect large accounts (tens of thousands of followers) to take a
  while.
- If Instagram asks you to confirm the login in the app or by email, do that
  and run the script again.
