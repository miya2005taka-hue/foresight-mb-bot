"""Diagnose our numeric / discrete distributions against what happened.

For every resolved numeric-family question we forecast, reconstruct the CDF we
submitted, read off our 10/50/90 quantiles in value space, and locate the
realized outcome inside our own distribution (the PIT value). A well calibrated
forecaster has PIT values spread uniformly over [0,1] and ~20% of outcomes
outside the 10-90 band; systematically tight distributions pile PIT up at 0/1.
Read-only.
"""

import json
import math
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


def unscale(x, scaling):
    """Map a normalized location in [0,1] to the question's value space."""
    lo = scaling.get("range_min")
    hi = scaling.get("range_max")
    zp = scaling.get("zero_point")
    if lo is None or hi is None:
        return None
    if zp is None:
        return lo + (hi - lo) * x
    deriv = (hi - zp) / (lo - zp)
    return lo + (hi - lo) * (deriv**x - 1) / (deriv - 1)


def scale(v, scaling):
    """Inverse of unscale."""
    lo = scaling.get("range_min")
    hi = scaling.get("range_max")
    zp = scaling.get("zero_point")
    if lo is None or hi is None or hi == lo:
        return None
    if zp is None:
        return (v - lo) / (hi - lo)
    deriv = (hi - zp) / (lo - zp)
    inner = (v - lo) / (hi - lo) * (deriv - 1) + 1
    if inner <= 0:
        return None
    return math.log(inner) / math.log(deriv)


def quantile_x(cdf, p):
    """Smallest normalized x whose cumulative probability reaches p."""
    n = len(cdf)
    for i, c in enumerate(cdf):
        if c >= p:
            if i == 0:
                return 0.0
            prev = cdf[i - 1]
            frac = 0 if c == prev else (p - prev) / (c - prev)
            return (i - 1 + frac) / (n - 1)
    return 1.0


def cdf_at(cdf, x):
    n = len(cdf)
    pos = max(0.0, min(1.0, x)) * (n - 1)
    i = int(pos)
    if i >= n - 1:
        return cdf[-1]
    return cdf[i] + (cdf[i + 1] - cdf[i]) * (pos - i)


status, me = get("/api/users/me/")
user_id = me.get("id")

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
        break
    results = body.get("results") or []
    posts.extend(results)
    if not body.get("next") or not results:
        break
    offset += 50

print(f"user={me.get('username')} resolved posts={len(posts)}")
print("\n    id type       q10          q50          q90          truth        PIT   inside  spot_peer")

rows = []
for post in posts:
    status, detail = get(f"/api/posts/{post['id']}/", with_cp="true")
    if not isinstance(detail, dict):
        continue
    q = detail.get("question") or {}
    if q.get("type") not in ("numeric", "discrete", "date"):
        continue
    my = q.get("my_forecasts") or {}
    latest = my.get("latest") or {}
    cdf = latest.get("forecast_values") or []
    sd = my.get("score_data") or {}
    scaling = q.get("scaling") or {}
    try:
        truth = float(q.get("resolution"))
    except (TypeError, ValueError):
        continue
    if len(cdf) < 3:
        continue

    q10 = unscale(quantile_x(cdf, 0.1), scaling)
    q50 = unscale(quantile_x(cdf, 0.5), scaling)
    q90 = unscale(quantile_x(cdf, 0.9), scaling)
    x_truth = scale(truth, scaling)
    pit = cdf_at(cdf, x_truth) if x_truth is not None else None
    inside = None
    if q10 is not None and q90 is not None:
        inside = q10 <= truth <= q90
    sp = sd.get("spot_peer_score")
    rows.append(
        {
            "id": post["id"],
            "type": q.get("type"),
            "q10": q10,
            "q50": q50,
            "q90": q90,
            "truth": truth,
            "pit": pit,
            "inside": inside,
            "spot_peer": sp,
            "range": (scaling.get("range_min"), scaling.get("range_max")),
            "open_lower": q.get("open_lower_bound"),
            "open_upper": q.get("open_upper_bound"),
            "title": str(detail.get("title"))[:45],
        }
    )


def fmt(v, w=12):
    if v is None:
        return " " * (w - 1) + "-"
    return format(v, f"{w}.4g")


for r in rows:
    pit_s = format(r["pit"], "6.3f") if isinstance(r["pit"], float) else "     -"
    ins_s = {True: "  yes ", False: "  NO  ", None: "   ?  "}[r["inside"]]
    sp_s = format(r["spot_peer"], "9.2f") if isinstance(r["spot_peer"], (int, float)) else "        -"
    print(
        f"{r['id']:>6} {r['type']:<9} {fmt(r['q10'])} {fmt(r['q50'])} {fmt(r['q90'])} "
        f"{fmt(r['truth'])} {pit_s} {ins_s} {sp_s}  {r['title']}"
    )
    print(
        f"       range={r['range']} open_lower={r['open_lower']} open_upper={r['open_upper']}"
    )

n = len(rows)
if n:
    outside = [r for r in rows if r["inside"] is False]
    pits = [r["pit"] for r in rows if isinstance(r["pit"], float)]
    print(f"\n=== numeric-family summary (n={n}) ===")
    print(
        f"  outcome outside our 10-90 band: {len(outside)}/{n} "
        f"(well calibrated would be ~20%)"
    )
    print(
        f"  their spot_peer sum: {sum(r['spot_peer'] for r in outside if isinstance(r['spot_peer'], (int, float))):.2f}"
    )
    if pits:
        lowtail = len([p for p in pits if p < 0.1])
        hightail = len([p for p in pits if p > 0.9])
        print(f"  PIT<0.1 (truth below our distribution): {lowtail}")
        print(f"  PIT>0.9 (truth above our distribution): {hightail}")
        print(f"  PIT values: {[round(p, 3) for p in sorted(pits)]}")

print("\nDONE (read-only)")
