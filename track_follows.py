#!/usr/bin/env python3
"""
Instagram follower / following tracker.

Logs into your Instagram account, resolves a target user (by username or by
display name such as "neta tuvian"), saves a snapshot of everyone the target
follows and everyone who follows them, and reports what changed since the
previous snapshot.

Run it once to create the baseline, then re-run whenever you want to see
new / removed followers and follows.

Usage:
    python track_follows.py                 # uses IG_TARGET from .env
    python track_follows.py --target neta.tuvian
    python track_follows.py --target "neta tuvian"
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    LoginRequired,
    TwoFactorRequired,
    UserNotFound,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SESSION_FILE = BASE_DIR / "session.json"


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        sys.exit("\nInteractive input required but not available. "
                 "Run this script from a terminal.")


def build_client(username: str, password: str) -> Client:
    cl = Client()
    cl.delay_range = [1, 3]  # small random delay between requests, be gentle
    cl.challenge_code_handler = lambda user, choice: _prompt(
        f"Instagram sent a verification code via {choice}. Enter it: "
    )

    # Re-use a saved session when possible so we don't log in every run.
    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
            cl.login(username, password)
            cl.get_timeline_feed()  # cheap call that fails if session is stale
            print("Logged in using saved session.")
            return cl
        except (LoginRequired, ChallengeRequired, Exception) as exc:  # noqa: BLE001
            print(f"Saved session is no longer valid ({exc.__class__.__name__}), "
                  "logging in again.")
            cl = Client()
            cl.delay_range = [1, 3]
            cl.challenge_code_handler = lambda user, choice: _prompt(
                f"Instagram sent a verification code via {choice}. Enter it: "
            )

    try:
        cl.login(username, password)
    except TwoFactorRequired:
        code = os.getenv("IG_2FA_CODE") or _prompt("Enter your 2FA code: ")
        cl.login(username, password, verification_code=code)

    cl.dump_settings(SESSION_FILE)
    print("Logged in and saved session to session.json.")
    return cl


# --------------------------------------------------------------------------- #
# Target resolution
# --------------------------------------------------------------------------- #
def resolve_target(cl: Client, target: str):
    """Return (user_id, username, full_name) for the requested target."""
    target = target.strip().lstrip("@")

    # 1) Try treating the input as a username (also try common variants of a
    #    display name like "neta tuvian" -> neta.tuvian / neta_tuvian / netatuvian).
    candidates = [target]
    if " " in target:
        parts = target.lower().split()
        candidates += [".".join(parts), "_".join(parts), "".join(parts)]
    for cand in candidates:
        if " " in cand:
            continue
        try:
            info = cl.user_info_by_username(cand)
            return str(info.pk), info.username, info.full_name or ""
        except UserNotFound:
            continue
        except Exception:  # noqa: BLE001
            continue

    # 2) Fall back to Instagram search by display name.
    results = cl.search_users(target)
    if not results:
        sys.exit(f"Could not find any Instagram user matching '{target}'.")

    exact = [u for u in results if (u.full_name or "").strip().lower() == target.lower()]
    if len(exact) == 1:
        u = exact[0]
        return str(u.pk), u.username, u.full_name or ""

    print(f"Several accounts match '{target}'. Pick one:")
    for i, u in enumerate(results[:10], 1):
        print(f"  {i}. @{u.username}  ({u.full_name})")
    choice = _prompt("Number (or press Enter for 1): ") or "1"
    try:
        u = results[int(choice) - 1]
    except (ValueError, IndexError):
        sys.exit("Invalid choice.")
    print(f"Tip: set IG_TARGET={u.username} in .env to skip this prompt next time.")
    return str(u.pk), u.username, u.full_name or ""


# --------------------------------------------------------------------------- #
# Snapshot + diff
# --------------------------------------------------------------------------- #
def to_map(users) -> dict:
    """{user_id: {username, full_name}} from instagrapi UserShort dicts."""
    return {
        str(pk): {"username": u.username, "full_name": u.full_name or ""}
        for pk, u in users.items()
    }


def take_snapshot(cl: Client, user_id: str, username: str, full_name: str) -> dict:
    print(f"Fetching who @{username} follows ...")
    following = to_map(cl.user_following(user_id, use_cache=False))
    print(f"  {len(following)} following")
    print(f"Fetching who follows @{username} ...")
    followers = to_map(cl.user_followers(user_id, use_cache=False))
    print(f"  {len(followers)} followers")
    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "target": {"user_id": user_id, "username": username, "full_name": full_name},
        "following": following,
        "followers": followers,
    }


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
# Main
# --------------------------------------------------------------------------- #
def main():
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=os.getenv("IG_TARGET", "neta tuvian"),
                        help="Instagram username or display name to track")
    args = parser.parse_args()

    ig_user = os.getenv("IG_USERNAME")
    ig_pass = os.getenv("IG_PASSWORD")
    if not ig_user or not ig_pass:
        sys.exit("Set IG_USERNAME and IG_PASSWORD in .env (see .env.example).")

    cl = build_client(ig_user, ig_pass)
    user_id, username, full_name = resolve_target(cl, args.target)
    print(f"Tracking @{username} ({full_name}), id {user_id}")

    curr = take_snapshot(cl, user_id, username, full_name)

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

    (target_dir / f"snapshot_{stamp}.json").write_text(
        json.dumps(curr, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_file.write_text(json.dumps(curr, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSnapshot saved to {target_dir.relative_to(BASE_DIR)}/")


if __name__ == "__main__":
    main()
