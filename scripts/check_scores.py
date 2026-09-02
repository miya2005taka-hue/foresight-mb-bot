"""Read-only Metaculus status probe.

Runs in GitHub Actions so it can use the METACULUS_TOKEN secret. Posts
nothing: every request is a GET. Prints our bot's identity, the tournament's
question counts, our forecast/score records and the leaderboard standing.
The token itself is never printed.
"""

import json
import os
import sys
import time
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
    # Cloudflare rejects the stdlib User-Agent with error 1010, and bursts of
    # requests trip error 1015, so mimic a browser UA and pace the calls.
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    time.sleep(2)
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
user_id = me.get("id") if isinstance(me, dict) else None
username = me.get("username") if isinstance(me, dict) else None
print(f"users/me [{status}] user_id={user_id} username={username}")

# 2. Tournament facts
status, proj = get(f"/api/projects/tournaments/{TOURNAMENT_ID}/")
print(f"\n--- tournament {TOURNAMENT_ID} [{status}] ---")
if isinstance(proj, dict):
    for k in [
        "name",
        "slug",
        "prize_pool",
        "start_date",
        "close_date",
        "forecasting_end_date",
        "is_ongoing",
        "forecasters_count",
        "forecasts_count",
        "questions_count",
        "posts_count",
        "has_participated",
        "bot_leaderboard_status",
        "score_type",
        "default_permission",
        "timeline",
    ]:
        if k in proj:
            print(f"  {k}: {json.dumps(proj[k], ensure_ascii=False)[:300]}")
    print("  (other keys:", [k for k in sorted(proj) if k not in ("description",)][:40], ")")
else:
    print(str(proj)[:300])

# 3. Post counts (inspect the envelope shape first)
status, sample = get("/api/posts/", tournaments=TOURNAMENT_ID, limit=1)
print(f"\n--- posts envelope [{status}] ---")
if isinstance(sample, dict):
    print("  keys:", sorted(sample)[:15])
    for k in ("count", "total_count", "next"):
        if k in sample:
            print(f"  {k}: {sample[k]}")
    results = sample.get("results") or []
    if results:
        print("  first result keys:", sorted(results[0])[:30])
elif isinstance(sample, list):
    print("  list len:", len(sample))

for label, params in [
    ("open", {"statuses": "open"}),
    ("resolved", {"statuses": "resolved"}),
    ("ours", {"forecaster_id": user_id}),
    ("ours_resolved", {"forecaster_id": user_id, "statuses": "resolved"}),
]:
    status, body_ = get("/api/posts/", tournaments=TOURNAMENT_ID, limit=1, **params)
    n = body_.get("count") if isinstance(body_, dict) else (len(body_) if isinstance(body_, list) else None)
    print(f"  posts[{label}] status={status} count={n}")

# 4. Leaderboard candidates
for path, params in [
    (f"/api/leaderboards/project/{TOURNAMENT_ID}/", {}),
    (f"/api/projects/tournaments/{TOURNAMENT_ID}/leaderboard/", {}),
    ("/api/leaderboards/", {"projectId": TOURNAMENT_ID}),
    (f"/api/projects/{TOURNAMENT_ID}/", {}),
]:
    status, body_ = get(path, **params)
    print(f"\n--- GET {path} {params} [{status}] ---")
    if isinstance(body_, dict):
        print("  keys:", sorted(body_)[:20])
        entries = body_.get("entries") or body_.get("leaderboard") or []
        if isinstance(entries, dict):
            entries = entries.get("entries", [])
        if entries:
            print("  entries:", len(entries))
            def uid(e):
                u = e.get("user")
                return u.get("id") if isinstance(u, dict) else (e.get("user_id") or u)
            ours = [e for e in entries if uid(e) == user_id]
            print("  OUR ENTRY:", json.dumps(ours, ensure_ascii=False)[:700] or "not found")
            print("  TOP 5:", json.dumps(entries[:5], ensure_ascii=False)[:900])
    else:
        print("  ", str(body_)[:200].replace("\n", " "))

# 5. Our resolved questions with scores
status, mine = get(
    "/api/posts/",
    tournaments=TOURNAMENT_ID,
    forecaster_id=user_id,
    statuses="resolved",
    limit=50,
)
print(f"\n--- our resolved posts [{status}] ---")
if isinstance(mine, dict):
    results = mine.get("results") or []
    print("  n:", len(results))
    for r in results[:20]:
        q = r.get("question") or {}
        scores = q.get("my_forecasts", {}) if isinstance(q, dict) else {}
        print(
            f"  id={r.get('id')} status={r.get('status')} title={str(r.get('title'))[:60]!r} "
            f"resolution={q.get('resolution')} score_keys={sorted(scores)[:6] if isinstance(scores, dict) else scores}"
        )

print("\nDONE (read-only; nothing was posted)")
