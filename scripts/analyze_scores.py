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
    my = q.get("my_forecasts") or {}
    sd = (my.get("score_data") or {}) if isinstance(my, dict) else {}
    latest = (my.get("latest") or {}) if isinstance(my, dict) else {}
    values = latest.get("forecast_values") or []
    options = q.get("options") or []
    resolution = q.get("resolution")
    qtype = q.get("type")

    # Probability we placed on the outcome that actually happened.
    p_truth = None
    if qtype == "binary" and len(values) == 2 and resolution in ("yes", "no"):
        p_truth = values[1] if resolution == "yes" else values[0]
    elif qtype == "multiple_choice" and options and resolution in options:
        idx = options.index(resolution)
        if idx < len(values):
            p_truth = values[idx]

    rows.append(
        {
            "id": pid,
            "type": qtype,
            "title": str(detail.get("title"))[:55],
            "resolution": str(resolution)[:28],
            "p_truth": None if p_truth is None else round(p_truth, 4),
            "spot_peer": sd.get("spot_peer_score"),
            "peer": sd.get("peer_score"),
            "baseline": sd.get("baseline_score"),
            "coverage": sd.get("coverage"),
        }
    )

print(f"\n=== per-question ({len(rows)} rows, sorted by spot_peer) ===")
print("    id type                spot_peer   baseline  p_truth   cov  resolution | title")
ranked = sorted(rows, key=lambda r: (r["spot_peer"] is None, r["spot_peer"] or 0))
for r in ranked:
    sp = r["spot_peer"]
    bl = r["baseline"]
    cv = r["coverage"]
    pt = r["p_truth"]
    sp_s = format(sp, "10.2f") if isinstance(sp, (int, float)) else "         -"
    bl_s = format(bl, "10.2f") if isinstance(bl, (int, float)) else "         -"
    pt_s = format(pt, "8.3f") if isinstance(pt, (int, float)) else "       -"
    cv_s = format(cv, "5.2f") if isinstance(cv, (int, float)) else "    -"
    print(
        f"{r['id']:>6} {str(r['type']):<16} {sp_s} {bl_s} {pt_s} {cv_s}  "
        f"{r['resolution']} | {r['title']}"
    )

scored = [r for r in rows if isinstance(r["spot_peer"], (int, float))]
print(f"\n=== spot_peer totals (leaderboard metric) ===")
print(f"  questions scored: {len(scored)}  total={sum(r['spot_peer'] for r in scored):.2f}")
by_type = {}
for r in scored:
    by_type.setdefault(r["type"], []).append(r["spot_peer"])
for t, vs in sorted(by_type.items()):
    print(f"  {t:<16} n={len(vs):<3} sum={sum(vs):9.2f} mean={sum(vs)/len(vs):8.2f} worst={min(vs):8.2f} best={max(vs):8.2f}")

neg = [r for r in scored if r["spot_peer"] < 0]
pos = [r for r in scored if r["spot_peer"] >= 0]
print(f"  negative questions: {len(neg)} (sum {sum(r['spot_peer'] for r in neg):.2f}) / "
      f"positive: {len(pos)} (sum {sum(r['spot_peer'] for r in pos):.2f})")
worst = sorted(scored, key=lambda r: r["spot_peer"])[:5]
print(f"  worst 5 account for {sum(r['spot_peer'] for r in worst):.2f} "
      f"({100*sum(r['spot_peer'] for r in worst)/sum(r['spot_peer'] for r in scored):.0f}% of the loss)")

conf = [r for r in scored if isinstance(r["p_truth"], (int, float))]
if conf:
    print(f"\n=== calibration on binary / multiple choice (n={len(conf)}) ===")
    buckets = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
    for lo, hi in buckets:
        sel = [r for r in conf if lo <= r["p_truth"] < hi]
        if sel:
            print(
                f"  p(truth) in [{lo:.1f},{hi:.1f}): n={len(sel):<3} "
                f"spot_peer sum={sum(r['spot_peer'] for r in sel):9.2f}"
            )
    overconfident = [r for r in conf if r["p_truth"] < 0.2]
    print(f"  outcomes we gave <20%: {len(overconfident)}/{len(conf)} "
          f"(sum {sum(r['spot_peer'] for r in overconfident):.2f})")

print(f"\nDONE (read-only)")
