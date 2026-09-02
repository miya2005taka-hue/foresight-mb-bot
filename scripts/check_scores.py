"""Read-only Metaculus status probe.

Runs in GitHub Actions so it can use the METACULUS_TOKEN secret. Posts
nothing: every request is a GET. Prints our bot's identity, the tournament's
question counts, our forecast/score records and the leaderboard standing.
The token itself is never printed.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.metaculus.com"
TOURNAMENT_ID = int(os.environ.get("TOURNAMENT_ID", "33022"))
TOKEN = os.environ.get("METACULUS_TOKEN", "")

if not TOKEN:
    print("METACULUS_TOKEN missing")
    sys.exit(1)


def get(path, **params):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Token {TOKEN}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # network-level
        return None, f"{type(e).__name__}: {e}"


def show(label, status, body, keys_only=False):
    print(f"\n--- {label} [{status}] ---")
    if isinstance(body, dict):
        print("keys:", sorted(body)[:25])
        if not keys_only:
            print(json.dumps(body, ensure_ascii=False)[:1200])
    else:
        print(str(body)[:600])


# 1. Who are we
status, me = get("/api/users/me/")
show("users/me", status, me, keys_only=True)
user_id = me.get("id") if isinstance(me, dict) else None
username = me.get("username") if isinstance(me, dict) else None
print(f"user_id={user_id} username={username}")

# 2. Tournament shape
for label, params in [
    ("all posts", {}),
    ("open", {"statuses": "open"}),
    ("resolved", {"statuses": "resolved"}),
]:
    status, body = get("/api/posts/", tournaments=TOURNAMENT_ID, limit=1, **params)
    count = body.get("count") if isinstance(body, dict) else body
    print(f"posts[{label}] status={status} count={count}")

# 3. Our forecasts in this tournament
status, body = get(
    "/api/posts/",
    tournaments=TOURNAMENT_ID,
    forecaster_id=user_id,
    limit=1,
)
print(
    "posts[forecast by us] status=",
    status,
    "count=",
    body.get("count") if isinstance(body, dict) else body,
)

# 4. Leaderboard probes (endpoint shape is not documented publicly)
for path, params in [
    ("/api/leaderboards/", {"project": TOURNAMENT_ID}),
    ("/api/leaderboards/", {"project_id": TOURNAMENT_ID}),
    (f"/api/projects/{TOURNAMENT_ID}/leaderboard/", {}),
    (f"/api/projects/tournaments/{TOURNAMENT_ID}/", {}),
    ("/api/projects/tournaments/", {"slug": "summer-futureeval-2026"}),
]:
    status, body = get(path, **params)
    show(f"GET {path} {params}", status, body, keys_only=True)
    if isinstance(body, dict):
        entries = body.get("entries") or body.get("leaderboard") or []
        if isinstance(entries, list) and entries:
            print(f"entries={len(entries)}")
            ours = [
                e
                for e in entries
                if isinstance(e, dict)
                and (
                    e.get("user_id") == user_id
                    or (e.get("user") or {}).get("id") == user_id
                    if isinstance(e.get("user"), dict)
                    else e.get("user") == user_id
                )
            ]
            print("OUR ENTRY:", json.dumps(ours, ensure_ascii=False)[:800] or "not found")
            print("TOP 5:", json.dumps(entries[:5], ensure_ascii=False)[:800])

# 5. Our scores (per-question), if the endpoint exists
for path, params in [
    ("/api/scores/", {"user": user_id, "project": TOURNAMENT_ID}),
    (f"/api/users/{user_id}/scores/", {}),
]:
    status, body = get(path, **params)
    show(f"GET {path} {params}", status, body, keys_only=True)

print("\nDONE (read-only; nothing was posted)")
