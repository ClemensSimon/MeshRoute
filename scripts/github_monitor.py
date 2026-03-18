"""
MeshRoute GitHub Feedback Monitor

Checks GitHub Discussions, Issues, and PRs for new activity.
Sends notifications via MQTT to Home Assistant.

Usage:
    python github_monitor.py              # check once
    python github_monitor.py --daemon     # run every 10 minutes
    python github_monitor.py --interval 5 # custom interval (minutes)

Requires:
    GITHUB_TOKEN from Credential Gate
    MQTT_PASS from Credential Gate (optional, for notifications)
"""

import json
import time
import sys
import os
import ssl
import urllib.request
import argparse
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────

REPO_OWNER = "ClemensSimon"
REPO_NAME = "MeshRoute"
CREDENTIAL_GATE = "https://192.168.178.131:8046"
MQTT_BROKER = "192.168.178.129"
MQTT_PORT = 1883
MQTT_TOPIC = "simon42/meshroute/github"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github_monitor_state.json")

# ── Credential Gate ─────────────────────────────────────────────

def get_credential(name):
    """Fetch credential from Credential Gate."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(f"{CREDENTIAL_GATE}/credentials/{name}")
        resp = urllib.request.urlopen(req, context=ctx, timeout=5)
        data = json.loads(resp.read())
        return data.get("value", "")
    except Exception as e:
        print(f"[WARN] Could not get credential {name}: {e}")
        return None

# ── GitHub API ──────────────────────────────────────────────────

def github_graphql(token, query, variables=None):
    """Execute a GitHub GraphQL query."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=data,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())

def github_rest(token, endpoint):
    """Execute a GitHub REST API call."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/{endpoint}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())

# ── Check Functions ─────────────────────────────────────────────

def check_discussions(token):
    """Check for new discussion comments."""
    query = """query {
        repository(owner: "%s", name: "%s") {
            discussions(first: 10, orderBy: {field: UPDATED_AT, direction: DESC}) {
                nodes {
                    number
                    title
                    url
                    updatedAt
                    comments(last: 5) {
                        totalCount
                        nodes {
                            author { login }
                            body
                            createdAt
                            url
                        }
                    }
                    reactions { totalCount }
                }
            }
        }
    }""" % (REPO_OWNER, REPO_NAME)

    result = github_graphql(token, query)
    discussions = result.get("data", {}).get("repository", {}).get("discussions", {}).get("nodes", [])

    items = []
    for d in discussions:
        items.append({
            "type": "discussion",
            "number": d["number"],
            "title": d["title"],
            "url": d["url"],
            "updated_at": d["updatedAt"],
            "comment_count": d["comments"]["totalCount"],
            "reaction_count": d["reactions"]["totalCount"],
            "comments": [
                {
                    "author": c["author"]["login"] if c["author"] else "ghost",
                    "body": c["body"][:200],
                    "created_at": c["createdAt"],
                    "url": c["url"],
                }
                for c in d["comments"]["nodes"]
            ],
        })
    return items

def check_issues(token):
    """Check for new issues."""
    try:
        issues = github_rest(token, "issues?state=all&per_page=10&sort=updated")
    except Exception:
        return []

    items = []
    for i in issues:
        if "pull_request" in i:
            continue  # skip PRs (they appear in issues API too)
        items.append({
            "type": "issue",
            "number": i["number"],
            "title": i["title"],
            "url": i["html_url"],
            "state": i["state"],
            "author": i["user"]["login"],
            "created_at": i["created_at"],
            "updated_at": i["updated_at"],
            "comment_count": i["comments"],
        })
    return items

def check_pulls(token):
    """Check for new pull requests."""
    try:
        pulls = github_rest(token, "pulls?state=all&per_page=10&sort=updated")
    except Exception:
        return []

    items = []
    for p in pulls:
        items.append({
            "type": "pr",
            "number": p["number"],
            "title": p["title"],
            "url": p["html_url"],
            "state": p["state"],
            "author": p["user"]["login"],
            "created_at": p["created_at"],
            "updated_at": p["updated_at"],
        })
    return items

def check_stars(token):
    """Check star count."""
    try:
        repo = github_rest(token, "")
        return repo.get("stargazers_count", 0), repo.get("forks_count", 0), repo.get("watchers_count", 0)
    except Exception:
        return 0, 0, 0

# ── State Management ────────────────────────────────────────────

def load_state():
    """Load last known state."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_check": None, "known_comments": {}, "stars": 0}

def save_state(state):
    """Save current state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── MQTT Notification ───────────────────────────────────────────

_mqtt_pass_cache = None

def _get_mqtt_pass():
    global _mqtt_pass_cache
    if not _mqtt_pass_cache:
        _mqtt_pass_cache = get_credential("MQTT_PASS")
    return _mqtt_pass_cache

def send_mqtt(message, topic=MQTT_TOPIC):
    """Send notification via MQTT."""
    mqtt_pass = _get_mqtt_pass()
    if not mqtt_pass:
        print(f"[MQTT] No MQTT credentials — skipping notification")
        return False

    try:
        import paho.mqtt.publish as publish
        publish.single(
            topic,
            payload=json.dumps(message) if isinstance(message, dict) else str(message),
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            auth={"username": "simon", "password": mqtt_pass},
            retain=True,
        )
        return True
    except ImportError:
        print("[MQTT] paho-mqtt not installed — pip install paho-mqtt")
        return False
    except Exception as e:
        print(f"[MQTT] Error: {e}")
        return False

def publish_status(stars, forks, discussions, issues, prs, new_count):
    """Publish full status to MQTT for HA + React frontend."""
    status = {
        "stars": stars,
        "forks": forks,
        "discussions": discussions,
        "issues": issues,
        "pull_requests": prs,
        "new_activity": new_count,
        "last_check": datetime.now(timezone.utc).isoformat(),
        "repo_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}",
        "simulator_url": f"https://{REPO_OWNER.lower()}.github.io/{REPO_NAME}/simulator.html",
    }
    send_mqtt(status, f"{MQTT_TOPIC}/status")
    send_mqtt({"count": stars}, f"{MQTT_TOPIC}/stars")
    if new_count > 0:
        send_mqtt("true", f"{MQTT_TOPIC}/new_feedback")
    return status

# ── Main Check ──────────────────────────────────────────────────

def run_check(token, notify=True):
    """Run a full check and report new activity."""
    state = load_state()
    now = datetime.now(timezone.utc).isoformat()
    new_items = []

    print(f"\n{'='*60}")
    print(f"  MeshRoute GitHub Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # Stars
    stars, forks, watchers = check_stars(token)
    old_stars = state.get("stars", 0)
    if stars > old_stars:
        diff = stars - old_stars
        print(f"\n  NEW STARS: +{diff} (total: {stars})")
        new_items.append({"type": "stars", "count": stars, "new": diff})
    elif stars > 0:
        print(f"\n  Stars: {stars} | Forks: {forks} | Watchers: {watchers}")
    state["stars"] = stars

    # Discussions
    discussions = check_discussions(token)
    known_comments = state.get("known_comments", {})

    for d in discussions:
        key = f"discussion-{d['number']}"
        old_count = known_comments.get(key, 0)
        new_count = d["comment_count"]

        if new_count > old_count:
            new_comments = d["comments"][-(new_count - old_count):]
            print(f"\n  DISCUSSION #{d['number']}: {d['title']}")
            print(f"  {d['url']}")
            print(f"  Reactions: {d['reaction_count']} | Comments: {new_count} (+{new_count - old_count} new)")
            for c in new_comments:
                author = c["author"]
                if author == REPO_OWNER.lower() or author == "ClemensSimon":
                    continue  # skip own comments
                print(f"\n    @{author} ({c['created_at'][:10]}):")
                print(f"    {c['body']}")
                print(f"    → {c['url']}")
                new_items.append({
                    "type": "discussion_comment",
                    "discussion": d["number"],
                    "author": author,
                    "body": c["body"][:200],
                    "url": c["url"],
                })

        known_comments[key] = new_count

    # Issues
    issues = check_issues(token)
    for i in issues:
        key = f"issue-{i['number']}"
        if key not in known_comments:
            if i["author"] != REPO_OWNER:
                print(f"\n  NEW ISSUE #{i['number']}: {i['title']}")
                print(f"  By @{i['author']} | {i['url']}")
                new_items.append({"type": "issue", "number": i["number"],
                                  "title": i["title"], "author": i["author"], "url": i["url"]})
        old_count = known_comments.get(key, 0)
        if i["comment_count"] > old_count:
            print(f"  Issue #{i['number']}: +{i['comment_count'] - old_count} new comments")
        known_comments[key] = i["comment_count"]

    # PRs
    pulls = check_pulls(token)
    for p in pulls:
        key = f"pr-{p['number']}"
        if key not in known_comments:
            if p["author"] != REPO_OWNER:
                print(f"\n  NEW PR #{p['number']}: {p['title']}")
                print(f"  By @{p['author']} | {p['url']}")
                new_items.append({"type": "pr", "number": p["number"],
                                  "title": p["title"], "author": p["author"], "url": p["url"]})
        known_comments[key] = 0

    state["known_comments"] = known_comments
    state["last_check"] = now
    save_state(state)

    # Count totals for status
    total_discussions = len(discussions)
    total_issues = len(issues)
    total_prs = len(pulls)

    # Publish full status to MQTT (always, for React dashboard)
    if notify:
        publish_status(stars, forks, total_discussions, total_issues, total_prs, len(new_items))
        # Publish each new activity item
        for item in new_items:
            send_mqtt(item, f"{MQTT_TOPIC}/activity")

    # Summary
    if not new_items:
        print("\n  No new activity since last check.")
    else:
        print(f"\n  {len(new_items)} new item(s) — MQTT notifications sent.")

    print(f"\n{'='*60}\n")
    return new_items

# ── Entry Point ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MeshRoute GitHub Feedback Monitor")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=10, help="Check interval in minutes (default: 10)")
    parser.add_argument("--no-mqtt", action="store_true", help="Disable MQTT notifications")
    args = parser.parse_args()

    token = get_credential("GITHUB_TOKEN")
    if not token:
        print("FATAL: No GITHUB_TOKEN available. Authenticate at Credential Gate.")
        sys.exit(1)

    if args.daemon:
        print(f"Running as daemon — checking every {args.interval} minutes")
        print("Press Ctrl+C to stop.\n")
        while True:
            try:
                run_check(token, notify=not args.no_mqtt)
                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print("\nStopped.")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
                time.sleep(60)  # retry in 1 minute
    else:
        run_check(token, notify=not args.no_mqtt)

if __name__ == "__main__":
    main()
