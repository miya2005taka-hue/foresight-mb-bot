"""Per-question post-mortem of our tournament forecasts (read-only).

Pulls every resolved question we forecast in the tournament and extracts our
forecast, the community prediction, the resolution and every score field the
API exposes, so the loss can be attributed by question type and direction.
Nothing is posted.
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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def get(path, **params):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    time.sleep(1.5)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def walk(node, path=""):
    """Yield (path, value) for every scalar under node."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node[:3]):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, node


status, me = get("/api/users/me/")
user_id = me.get("id")
print(f"user={me.get('username')} id={user_id}")

# Collect every resolved question we forecast.
posts = []
offset = 0
while True:
    status, body = get(
        "/api/posts/",
        tournaments=TOURNAMENT_ID,
        forecaster_id=user_id,
        statuses="resolved",
        limit=50,
        offset=offset,
    )
    if not isinstance(body, dict):
        print("posts fetch failed:", status, str(body)[:200])
        break
    results = body.get("results") or []
    posts.extend(results)
    if not body.get("next") or not results:
        break
    offset += 50
print(f"resolved posts forecast by us: {len(posts)}")

# Structure probe on the first post so unknown score fields are discoverable.
if posts:
    status, detail = get(f"/api/posts/{posts[0]['id']}/", with_cp="true")
    print(f"\n--- structure probe post {posts[0]['id']} [{status}] ---")
    if isinstance(detail, dict):
        hits = [
            (p, v)
            for p, v in walk(detail)
            if "score" in p.lower() or "my_forecast" in p.lower()
        ]
        for p, v in hits[:40]:
            print(f"  {p} = {json.dumps(v, ensure_ascii=False)[:120]}")
        if not hits:
            print("  no score-like fields; top keys:", sorted(detail)[:25])
            q = detail.get("question")
            if isinstance(q, dict):
                print("  question keys:", sorted(q)[:30])

rows = []
for post in posts:
    pid = post["id"]
    status, detail = get(f"/api/posts/{pid}/", with_cp="true")
    if not isinstance(detail, dict):
        print(f"  post {pid} fetch failed [{status}]")
        continue
    q = detail.get("question") or {}
    qtype = q.get("type")
    scores = {
        p: v
        for p, v in walk(detail)
        if "score" in p.lower() and isinstance(v, (int, float))
    }
    my = q.get("my_forecasts") or {}
    latest = (my.get("latest") or {}) if isinstance(my, dict) else {}
    rows.append(
        {
            "id": pid,
            "type": qtype,
            "title": str(detail.get("title"))[:70],
            "resolution": q.get("resolution"),
            "my_forecast": latest.get("forecast_values")
            or latest.get("centers")
            or latest.get("probability_yes"),
            "scores": scores,
        }
    )

print(f"\n=== per-question rows: {len(rows)} ===")
for r in rows:
    print(json.dumps(r, ensure_ascii=False)[:600])

vals = [
    (r["id"], r["type"], v)
    for r in rows
    for k, v in r["scores"].items()
    if k.endswith("peer_score") or k.endswith("spot_peer_score")
]
if vals:
    total = sum(v for _, _, v in vals)
    print(f"\npeer-score records: {len(vals)} total={total:.2f}")
    by_type = {}
    for _, t, v in vals:
        by_type.setdefault(t, []).append(v)
    for t, vs in sorted(by_type.items()):
        print(f"  {t}: n={len(vs)} sum={sum(vs):.2f} mean={sum(vs)/len(vs):.2f}")

print("\nDONE (read-only)")
