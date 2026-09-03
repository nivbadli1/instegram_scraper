#!/usr/bin/env python3
"""
Instagram follower / following tracker (browser based).

Opens a real Chromium window with Playwright. The first time, you log in to
Instagram yourself in that window (password, Facebook login, 2FA - anything
works). The login is kept in ./browser_profile so later runs don't ask again.

Then the script resolves the target user (set IG_TARGET in .env, or pass
--target), downloads everyone they follow and everyone who follows them
through Instagram's own web API, saves a versioned snapshot (JSON + CSV), and
prints / logs what changed since the previous snapshot.

Usage:
    python track_follows.py                       # target from IG_TARGET in .env
    python track_follows.py --target neta_tuvian
    python track_follows.py --headed              # always show the browser
"""

import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, BrowserContext

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROFILE_DIR = BASE_DIR / "browser_profile"
IG_URL = "https://www.instagram.com/"
IG_APP_ID = "936619743392459"  # public app id the Instagram web client sends
PAGE_SIZE = 25
LOGIN_TIMEOUT_S = 600  # how long to wait for you to log in manually
MAX_VERIFY = 300       # max individual "is this person still there?" checks per run


# --------------------------------------------------------------------------- #
# Browser / login
# --------------------------------------------------------------------------- #
def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        sys.exit("\nInteractive input required but not available. "
                 "Run this script from a terminal.")


def _is_logged_in(context: BrowserContext) -> bool:
    return any(c["name"] == "ds_user_id" and c["value"]
               for c in context.cookies(IG_URL))


def _launch(pw, headed: bool) -> BrowserContext:
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=not headed,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        args=["--disable-blink-features=AutomationControlled"],
    )


def open_instagram(pw, headed: bool):
    """Return (context, page) with a logged-in Instagram tab."""
    context = _launch(pw, headed)
    if _is_logged_in(context):
        page = context.new_page()
        page.goto(IG_URL, wait_until="domcontentloaded")
        print("Using saved Instagram login from browser_profile/.")
        return context, page

    # Not logged in: make sure the window is visible so you can log in.
    if not headed:
        context.close()
        context = _launch(pw, headed=True)
    page = context.new_page()
    page.goto(IG_URL + "accounts/login/", wait_until="domcontentloaded")
    print("\nA browser window is open. Log in to Instagram there "
          "(Facebook login / 2FA are fine).")
    print("Waiting for you to finish ...")
    deadline = time.time() + LOGIN_TIMEOUT_S
    while time.time() < deadline:
        if _is_logged_in(context):
            time.sleep(3)  # let Instagram finish setting cookies
            print("Login detected, session saved to browser_profile/.")
            page.goto(IG_URL, wait_until="domcontentloaded")
            time.sleep(2)
            return context, page
        time.sleep(1)
    sys.exit("Timed out waiting for login.")


# --------------------------------------------------------------------------- #
# Instagram web API helpers (run inside the logged-in page)
# --------------------------------------------------------------------------- #
_FETCH_JS = """
async ([url, appId]) => {
    try {
        const r = await fetch(url, {
            headers: {"x-ig-app-id": appId, "x-requested-with": "XMLHttpRequest"},
            credentials: "include",
        });
        return {status: r.status, text: await r.text()};
    } catch (e) {
        return {status: 0, text: String(e)};  // network error, caller retries
    }
}
"""


def api_get(page: Page, path: str, retries: int = 4, fatal: bool = True):
    """GET an Instagram web API path. Returns parsed JSON, or None if not
    fatal and every attempt failed."""
    url = IG_URL + path.lstrip("/")
    for attempt in range(1, retries + 1):
        res = page.evaluate(_FETCH_JS, [url, IG_APP_ID])
        throttled = res["status"] == 429
        if res["status"] == 200:
            try:
                data = json.loads(res["text"])
            except json.JSONDecodeError:
                data = None  # probably an HTML login/challenge page, retry below
            if isinstance(data, dict) and data.get("status", "ok") == "ok":
                return data
            # 200 but {"status":"fail","message":"Please wait a few minutes"}
            throttled = True
        if res["status"] == 404 and not fatal:
            return None
        if res["status"] in (401, 403) and not _is_logged_in(page.context):
            sys.exit("Instagram logged you out. Delete browser_profile/ and run again.")
        if attempt == retries:
            break
        wait = 30 * (2 ** (attempt - 1)) if throttled else 3 * attempt
        what = "rate limit" if throttled else f"status {res['status']}"
        print(f"\n  Instagram {what}, waiting {wait}s before retry "
              f"({attempt}/{retries}) ...")
        time.sleep(wait)
    if fatal:
        sys.exit(f"Giving up on {path}. Instagram is rate limiting you; "
                 "wait an hour and run again.")
    return None


def profile_info(page: Page, username: str):
    """Return the profile dict from web_profile_info, or None."""
    data = api_get(page, f"api/v1/users/web_profile_info/?username={quote(username)}",
                   retries=2, fatal=False)
    try:
        return data["data"]["user"] or None
    except (KeyError, TypeError):
        return None


def resolve_target(page: Page, target: str):
    """Return (user_id, username, full_name, reported_followers, reported_following)."""
    target = target.strip().lstrip("@")

    def counts(u):
        try:
            return (u["edge_followed_by"]["count"], u["edge_follow"]["count"])
        except (KeyError, TypeError):
            return (None, None)

    # 1) Treat the input as a username (plus variants of a display name).
    candidates = [target]
    if " " in target:
        parts = target.lower().split()
        candidates += [".".join(parts), "_".join(parts), "".join(parts)]
    for cand in candidates:
        if " " in cand:
            continue
        u = profile_info(page, cand)
        if u:
            return (str(u["id"]), u["username"], u.get("full_name") or "", *counts(u))

    # 2) Fall back to Instagram's search box.
    data = api_get(page, f"api/v1/web/search/topsearch/?context=blended&query={quote(target)}")
    users = [x["user"] for x in data.get("users", [])]
    if not users:
        sys.exit(f"Could not find any Instagram user matching '{target}'.")

    exact_user = [u for u in users if u["username"].lower() == target.lower()]
    exact_name = [u for u in users if (u.get("full_name") or "").strip().lower() == target.lower()]
    if exact_user:
        u = exact_user[0]
    elif len(exact_name) == 1:
        u = exact_name[0]
    else:
        print(f"Several accounts match '{target}'. Pick one:")
        for i, u in enumerate(users[:10], 1):
            print(f"  {i}. @{u['username']}  ({u.get('full_name', '')})")
        choice = _prompt("Number (or press Enter for 1): ") or "1"
        try:
            u = users[int(choice) - 1]
        except (ValueError, IndexError):
            sys.exit("Invalid choice.")
        print(f"Tip: set IG_TARGET={u['username']} in .env to skip this prompt next time.")

    rep = (None, None)
    info = api_get(page, f"api/v1/users/{u['pk']}/info/", retries=2, fatal=False)
    try:
        rep = (info["user"]["follower_count"], info["user"]["following_count"])
    except (KeyError, TypeError):
        print("  Could not read the profile counts (rate limited); continuing without them.")
    return (str(u["pk"]), u["username"], u.get("full_name") or "", *rep)


def fetch_list(page: Page, user_id: str, kind: str, expected) -> dict:
    """kind is 'followers' or 'following'. Returns {user_id: {username, full_name}}.

    Instagram's web endpoint often stops handing out a next_max_id before the
    list is really finished (and at a different point each run). So whenever
    the cursor disappears we keep going by numeric offset, which the web
    client itself also uses, until a page brings nothing new.
    """
    users: dict = {}
    max_id = ""
    offset_mode = False
    empty_pages = 0
    while True:
        path = (f"api/v1/friendships/{user_id}/{kind}/?count={PAGE_SIZE}"
                f"&search_surface=follow_list_page")
        if max_id:
            path += f"&max_id={quote(str(max_id))}"
        data = api_get(page, path, retries=5)
        before = len(users)
        for u in data.get("users", []):
            users[str(u["pk"])] = {"username": u["username"],
                                   "full_name": u.get("full_name") or ""}
        print(f"\r  {len(users)} {kind} so far", end="", flush=True)

        got_new = len(users) > before
        empty_pages = 0 if got_new else empty_pages + 1
        cursor = data.get("next_max_id")
        done = expected is not None and len(users) >= expected
        if done or empty_pages >= 2:
            break
        if cursor and not offset_mode:
            max_id = cursor
        elif got_new:
            offset_mode = True
            max_id = str(len(users))
        else:
            break
        time.sleep(random.uniform(1.5, 3.0))
    print()
    if expected is None:
        print(f"  (profile count unavailable, so cannot check whether {len(users)} is complete)")
    elif len(users) < expected:
        print(f"  Profile shows {expected} {kind}, fetched {len(users)}. Instagram never "
              "returns the full list in one pass; the saved list fills in over runs.")
    return users


def verify_still_present(page: Page, user_id: str, kind: str, candidates: dict):
    """Instagram never hands out the complete list in one pass, so someone
    missing from today's fetch is not necessarily gone. Ask the list's own
    search box about each one. Returns (still_present, gone, unverified)."""
    still, gone, unverified = {}, {}, {}
    items = list(candidates.items())
    total = min(len(items), MAX_VERIFY)
    try:
        for i, (uid, u) in enumerate(items, 1):
            if i > MAX_VERIFY:
                unverified[uid] = u
                continue
            print(f"\r  Verifying {kind} not seen this run: {i}/{total} "
                  "(Ctrl-C to skip the rest and save)", end="", flush=True)
            path = (f"api/v1/friendships/{user_id}/{kind}/?count={PAGE_SIZE}"
                    f"&search_surface=follow_list_page&query={quote(u['username'])}")
            data = api_get(page, path, retries=5, fatal=False)
            if data is None:
                unverified[uid] = u
                continue
            hit = next((x for x in data.get("users", []) if str(x["pk"]) == uid), None)
            if not hit:  # the list search misses sometimes; try once more
                time.sleep(random.uniform(2.0, 3.0))
                data = api_get(page, path, retries=3, fatal=False) or {}
                hit = next((x for x in data.get("users", []) if str(x["pk"]) == uid), None)
            if hit:
                still[uid] = {"username": hit["username"], "full_name": hit.get("full_name") or ""}
            else:
                gone[uid] = u
            time.sleep(random.uniform(1.0, 2.0))
    except KeyboardInterrupt:
        checked = set(still) | set(gone) | set(unverified)
        for uid, u in items:
            if uid not in checked:
                unverified[uid] = u
        print("\n  Interrupted: remaining people kept as unverified, saving what we have.")
    if items:
        print()
    return still, gone, unverified


def reconcile(page: Page, prev: dict, curr: dict) -> dict:
    """Merge people from the previous snapshot who were not fetched this run
    but are confirmed still present, so the list converges to the truth.

    Someone the search cannot find is only reported as lost once that has
    happened on two consecutive runs; until then they stay in the snapshot
    with a 'missing_since' mark. Returns the people in that pending state."""
    pending = {"followers": {}, "following": {}}
    for kind in ("following", "followers"):
        missing = {k: v for k, v in prev[kind].items() if k not in curr[kind]}
        if not missing:
            continue
        still, gone, unverified = verify_still_present(page, curr["target"]["user_id"], kind, missing)
        curr[kind].update(still)
        curr[kind].update(unverified)  # assume present until we can check
        confirmed = 0
        for uid, u in gone.items():
            if u.get("missing_since"):
                confirmed += 1  # missing two runs in a row: really gone, drop it
            else:
                curr[kind][uid] = {**u, "missing_since": curr["taken_at"]}
                pending[kind][uid] = u
        msg = (f"  {kind}: {len(missing)} missing from this fetch, {len(still)} confirmed still there, "
               f"{len(pending[kind])} not found (will confirm next run), {confirmed} confirmed gone")
        if unverified:
            msg += f", {len(unverified)} left unverified (kept, checked next run)"
        print(msg)
    return pending


def take_snapshot(page: Page, user_id, username, full_name, rep_followers, rep_following) -> dict:
    print(f"Fetching who @{username} follows ...")
    following = fetch_list(page, user_id, "following", rep_following)
    print(f"Fetching who follows @{username} ...")
    followers = fetch_list(page, user_id, "followers", rep_followers)
    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "target": {"user_id": user_id, "username": username, "full_name": full_name,
                   "reported_followers": rep_followers,
                   "reported_following": rep_following},
        "following": following,
        "followers": followers,
    }


# --------------------------------------------------------------------------- #
# Diff / report
# --------------------------------------------------------------------------- #
def diff_lists(old: dict, new: dict):
    added = {k: new[k] for k in new.keys() - old.keys()}
    removed = {k: old[k] for k in old.keys() - new.keys()}
    renamed = {
        k: {"old": old[k]["username"], "new": new[k]["username"]}
        for k in new.keys() & old.keys()
        if old[k]["username"] != new[k]["username"]
    }
    return added, removed, renamed


def fmt(users: dict) -> str:
    if not users:
        return "    (none)"
    lines = []
    for u in sorted(users.values(), key=lambda x: x["username"].lower()):
        name = f"  ({u['full_name']})" if u["full_name"] else ""
        lines.append(f"    @{u['username']}{name}")
    return "\n".join(lines)


def report(prev: dict, curr: dict) -> dict:
    username = curr["target"]["username"]
    new_flw, lost_flw, ren_flw = diff_lists(prev["followers"], curr["followers"])
    new_fng, unf_fng, ren_fng = diff_lists(prev["following"], curr["following"])

    print("\n" + "=" * 60)
    print(f"Changes for @{username}")
    print(f"Previous snapshot: {prev['taken_at']}")
    print(f"Current snapshot:  {curr['taken_at']}")
    print("=" * 60)
    print(f"\nFollowers: {len(prev['followers'])} -> {len(curr['followers'])}")
    print(f"  New followers ({len(new_flw)}):\n{fmt(new_flw)}")
    print(f"  Lost followers ({len(lost_flw)}):\n{fmt(lost_flw)}")
    print(f"\nFollowing: {len(prev['following'])} -> {len(curr['following'])}")
    print(f"  Started following ({len(new_fng)}):\n{fmt(new_fng)}")
    print(f"  Unfollowed ({len(unf_fng)}):\n{fmt(unf_fng)}")
    renamed = {**ren_flw, **ren_fng}
    if renamed:
        print(f"\nUsername changes ({len(renamed)}):")
        for r in renamed.values():
            print(f"    @{r['old']} -> @{r['new']}")
    if not (new_flw or lost_flw or new_fng or unf_fng or renamed):
        print("\nNo changes since the previous snapshot.")

    return {
        "compared_at": curr["taken_at"],
        "previous_snapshot": prev["taken_at"],
        "new_followers": new_flw,
        "lost_followers": lost_flw,
        "new_following": new_fng,
        "unfollowed": unf_fng,
        "username_changes": renamed,
    }


# --------------------------------------------------------------------------- #
# CSV output
# --------------------------------------------------------------------------- #
def _append_csv(path: Path, header: list, rows: list) -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(header)
        w.writerows(rows)


def write_csvs(target_dir: Path, stamp: str, curr: dict, changes) -> None:
    taken = curr["taken_at"]
    t = curr["target"]

    # 1) One versioned CSV per run with the full lists.
    rows = []
    for kind in ("followers", "following"):
        for uid, u in sorted(curr[kind].items(), key=lambda x: x[1]["username"].lower()):
            rows.append([taken, kind, uid, u["username"], u["full_name"]])
    with (target_dir / f"snapshot_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["taken_at", "relation", "user_id", "username", "full_name"])
        w.writerows(rows)

    # 2) One line per run with the totals (easy to chart over time).
    _append_csv(
        target_dir / "history.csv",
        ["taken_at", "target", "followers", "following",
         "reported_followers", "reported_following",
         "new_followers", "lost_followers", "started_following", "unfollowed"],
        [[taken, t["username"], len(curr["followers"]), len(curr["following"]),
          t.get("reported_followers") or "", t.get("reported_following") or "",
          len(changes["new_followers"]) if changes else 0,
          len(changes["lost_followers"]) if changes else 0,
          len(changes["new_following"]) if changes else 0,
          len(changes["unfollowed"]) if changes else 0]],
    )

    # 3) Running log of every individual change across all runs.
    if changes:
        rows = []
        for key, label in (("new_followers", "new_follower"),
                            ("lost_followers", "lost_follower"),
                            ("new_following", "started_following"),
                            ("unfollowed", "unfollowed"),
                            ("possibly_lost_followers", "possibly_lost_follower"),
                            ("possibly_unfollowed", "possibly_unfollowed")):
            for uid, u in changes[key].items():
                rows.append([taken, label, uid, u["username"], u["full_name"]])
        for uid, r in changes["username_changes"].items():
            rows.append([taken, "username_change", uid, r["new"], f"was @{r['old']}"])
        if rows:
            _append_csv(target_dir / "changes.csv",
                        ["taken_at", "change", "user_id", "username", "full_name"], rows)


def save_and_compare(curr: dict, pending: dict = None) -> None:
    username = curr["target"]["username"]
    target_dir = DATA_DIR / username
    target_dir.mkdir(parents=True, exist_ok=True)
    latest_file = target_dir / "latest.json"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    n = 1
    while (target_dir / f"snapshot_{stamp}.json").exists():  # never overwrite a run
        n += 1
        stamp = f"{stamp.split('_v')[0]}_v{n}"

    changes = None
    if latest_file.exists():
        prev = json.loads(latest_file.read_text(encoding="utf-8"))
        changes = report(prev, curr)
        pending = pending or {"followers": {}, "following": {}}
        changes["possibly_lost_followers"] = pending["followers"]
        changes["possibly_unfollowed"] = pending["following"]
        if pending["followers"] or pending["following"]:
            print("\nNot found this run, will be reported as lost if still missing next run:")
            print(f"  Followers ({len(pending['followers'])}):\n{fmt(pending['followers'])}")
            print(f"  Following ({len(pending['following'])}):\n{fmt(pending['following'])}")
        (target_dir / f"changes_{stamp}.json").write_text(
            json.dumps(changes, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print("\nFirst run: baseline saved. Run again later to see changes.")

    dump = json.dumps(curr, indent=2, ensure_ascii=False)
    (target_dir / f"snapshot_{stamp}.json").write_text(dump, encoding="utf-8")
    latest_file.write_text(dump, encoding="utf-8")
    write_csvs(target_dir, stamp, curr, changes)
    rel = os.path.relpath(target_dir, BASE_DIR)
    print(f"\nSaved to {rel}/: snapshot_{stamp}.csv, history.csv"
          + (", changes.csv" if changes else ""))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=os.getenv("IG_TARGET"),
                        help="Instagram username to track (default: IG_TARGET from .env)")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser window even when already logged in")
    args = parser.parse_args()
    if not args.target:
        sys.exit("No target given. Set IG_TARGET=<username> in .env or pass --target.")

    with sync_playwright() as pw:
        context, page = open_instagram(pw, headed=args.headed)
        try:
            user_id, username, full_name, rep_flw, rep_fng = resolve_target(page, args.target)
            shown = f" ({rep_flw} followers, {rep_fng} following on profile)" if rep_flw else ""
            print(f"Tracking @{username} ({full_name}), id {user_id}{shown}")
            curr = take_snapshot(page, user_id, username, full_name, rep_flw, rep_fng)
            pending = None
            latest_file = DATA_DIR / username / "latest.json"
            if latest_file.exists():
                prev = json.loads(latest_file.read_text(encoding="utf-8"))
                pending = reconcile(page, prev, curr)
        finally:
            context.close()

    save_and_compare(curr, pending)


if __name__ == "__main__":
    main()
