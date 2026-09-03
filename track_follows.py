#!/usr/bin/env python3
"""
Instagram follower / following tracker (browser based).

Opens a real Chromium window with Playwright. The first time, you log in to
Instagram yourself in that window (password, Facebook login, 2FA - anything
works). The login is kept in ./browser_profile so later runs don't ask again.

Then the script resolves the target user (username or display name such as
"neta tuvian"), downloads everyone they follow and everyone who follows them
through Instagram's own web API, saves a snapshot, and prints what changed
since the previous snapshot.

Usage:
    python track_follows.py                       # target from IG_TARGET or default
    python track_follows.py --target neta.tuvian
    python track_follows.py --target "neta tuvian"
    python track_follows.py --headed              # always show the browser
"""

import argparse
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
PAGE_SIZE = 50
LOGIN_TIMEOUT_S = 600  # how long to wait for you to log in manually


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
            time.sleep(2)  # let Instagram finish setting cookies
            print("Login detected, session saved to browser_profile/.")
            page.goto(IG_URL, wait_until="domcontentloaded")
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


def api_get(page: Page, path: str, retries: int = 4) -> dict:
    url = IG_URL + path.lstrip("/")
    for attempt in range(1, retries + 1):
        res = page.evaluate(_FETCH_JS, [url, IG_APP_ID])
        if res["status"] == 200:
            try:
                return json.loads(res["text"])
            except json.JSONDecodeError:
                pass  # probably an HTML login/challenge page, retry below
        if res["status"] in (401, 403) and not _is_logged_in(page.context):
            sys.exit("Instagram logged you out. Delete browser_profile/ and run again.")
        wait = 15 * attempt if res["status"] == 429 else 3 * attempt
        print(f"  Instagram answered {res['status']}, retrying in {wait}s "
              f"({attempt}/{retries}) ...")
        time.sleep(wait)
    sys.exit(f"Giving up on {path}. Instagram may be rate limiting you; "
             "try again in an hour.")


def resolve_target(page: Page, target: str):
    """Return (user_id, username, full_name)."""
    target = target.strip().lstrip("@")

    candidates = [target]
    if " " in target:
        parts = target.lower().split()
        candidates += [".".join(parts), "_".join(parts), "".join(parts)]
    for cand in candidates:
        if " " in cand:
            continue
        res = page.evaluate(
            _FETCH_JS,
            [f"{IG_URL}api/v1/users/web_profile_info/?username={quote(cand)}", IG_APP_ID],
        )
        if res["status"] == 200:
            try:
                u = json.loads(res["text"])["data"]["user"]
            except (json.JSONDecodeError, KeyError, TypeError):
                u = None
            if u:
                return str(u["id"]), u["username"], u.get("full_name") or ""

    # Fall back to Instagram's search box.
    data = api_get(page, f"api/v1/web/search/topsearch/?context=blended&query={quote(target)}")
    users = [x["user"] for x in data.get("users", [])]
    if not users:
        sys.exit(f"Could not find any Instagram user matching '{target}'.")

    exact = [u for u in users if (u.get("full_name") or "").strip().lower() == target.lower()]
    if len(exact) == 1:
        u = exact[0]
        return str(u["pk"]), u["username"], u.get("full_name") or ""

    print(f"Several accounts match '{target}'. Pick one:")
    for i, u in enumerate(users[:10], 1):
        print(f"  {i}. @{u['username']}  ({u.get('full_name', '')})")
    choice = _prompt("Number (or press Enter for 1): ") or "1"
    try:
        u = users[int(choice) - 1]
    except (ValueError, IndexError):
        sys.exit("Invalid choice.")
    print(f"Tip: set IG_TARGET={u['username']} in .env to skip this prompt next time.")
    return str(u["pk"]), u["username"], u.get("full_name") or ""


def fetch_list(page: Page, user_id: str, kind: str) -> dict:
    """kind is 'followers' or 'following'. Returns {user_id: {username, full_name}}."""
    users, max_id = {}, ""
    while True:
        path = f"api/v1/friendships/{user_id}/{kind}/?count={PAGE_SIZE}"
        if max_id:
            path += f"&max_id={quote(max_id)}"
        data = api_get(page, path)
        for u in data.get("users", []):
            users[str(u["pk"])] = {"username": u["username"],
                                   "full_name": u.get("full_name") or ""}
        print(f"\r  {len(users)} {kind} so far", end="", flush=True)
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(random.uniform(1.0, 2.5))
    print()
    return users


def take_snapshot(page: Page, user_id: str, username: str, full_name: str) -> dict:
    print(f"Fetching who @{username} follows ...")
    following = fetch_list(page, user_id, "following")
    print(f"Fetching who follows @{username} ...")
    followers = fetch_list(page, user_id, "followers")
    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "target": {"user_id": user_id, "username": username, "full_name": full_name},
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


def save_and_compare(curr: dict) -> None:
    username = curr["target"]["username"]
    target_dir = DATA_DIR / username
    target_dir.mkdir(parents=True, exist_ok=True)
    latest_file = target_dir / "latest.json"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if latest_file.exists():
        prev = json.loads(latest_file.read_text(encoding="utf-8"))
        changes = report(prev, curr)
        (target_dir / f"changes_{stamp}.json").write_text(
            json.dumps(changes, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print("\nFirst run: baseline saved. Run again later to see changes.")

    dump = json.dumps(curr, indent=2, ensure_ascii=False)
    (target_dir / f"snapshot_{stamp}.json").write_text(dump, encoding="utf-8")
    latest_file.write_text(dump, encoding="utf-8")
    print(f"\nSnapshot saved to {os.path.relpath(target_dir, BASE_DIR)}/")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=os.getenv("IG_TARGET", "neta tuvian"),
                        help="Instagram username or display name to track")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser window even when already logged in")
    args = parser.parse_args()

    with sync_playwright() as pw:
        context, page = open_instagram(pw, headed=args.headed)
        try:
            user_id, username, full_name = resolve_target(page, args.target)
            print(f"Tracking @{username} ({full_name}), id {user_id}")
            curr = take_snapshot(page, user_id, username, full_name)
        finally:
            context.close()

    save_and_compare(curr)


if __name__ == "__main__":
    main()
