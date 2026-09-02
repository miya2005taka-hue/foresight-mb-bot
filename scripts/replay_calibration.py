"""Counterfactual replay: what would calibration have done to our 22 questions?

Recovers the peer term from each observed spot peer score, then re-scores the
same questions under (i) a clip floor for binary / multiple choice and (ii) a
sharpening factor for the numeric family. Adds a leave-one-out check so the
chosen constant is not read off the same data, and a peer-generated simulation
that asks whether the observed tail loss is what a calibrated forecaster would
suffer by chance. Read-only against Metaculus.
"""

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calib_lib as cl

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


def scale_to_x(v, scaling):
    lo = scaling.get("range_min")
    hi = scaling.get("range_max")
    zp = scaling.get("zero_point")
    if lo is None or hi is None or hi == lo:
        return None
    if zp is None:
        return (v - lo) / (hi - lo)
    deriv = (hi - zp) / (lo - zp)
    inner = (v - lo) / (hi - lo) * (deriv - 1) + 1
    return math.log(inner) / math.log(deriv) if inner > 0 else None


def cdf_at(cdf, x):
    n = len(cdf)
    pos = max(0.0, min(1.0, x)) * (n - 1)
    i = int(pos)
    if i >= n - 1:
        return cdf[-1]
    return cdf[i] + (cdf[i + 1] - cdf[i]) * (pos - i)


def density_at(cdf, x, h=0.005):
    """Density in normalized x units."""
    return max((cdf_at(cdf, x + h) - cdf_at(cdf, x - h)) / (2 * h), 1e-9)


def median_x(cdf):
    for i, c in enumerate(cdf):
        if c >= 0.5:
            return i / (len(cdf) - 1)
    return 0.5


def rescale_cdf(cdf, lam):
    """Rebuild the CDF squeezed (lam<1) or widened (lam>1) about its median.

    Mass is never renormalised away: whatever the transform pushes past a bound
    stays piled at that bound, exactly like Metaculus' out-of-range mass. At
    lam=1 this must return the input unchanged, which the caller asserts.
    """
    n = len(cdf)
    m = median_x(cdf)
    return [cdf_at(cdf, m + (i / (n - 1) - m) / lam) for i in range(n)]


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

cat_records = []
num_records = []
for post in posts:
    status, detail = get(f"/api/posts/{post['id']}/", with_cp="true")
    if not isinstance(detail, dict):
        continue
    q = detail.get("question") or {}
    my = q.get("my_forecasts") or {}
    sd = my.get("score_data") or {}
    latest = my.get("latest") or {}
    values = latest.get("forecast_values") or []
    sp = sd.get("spot_peer_score")
    if not isinstance(sp, (int, float)):
        continue
    qtype = q.get("type")
    resolution = q.get("resolution")

    if qtype == "binary" and len(values) == 2 and resolution in ("yes", "no"):
        p = values[1] if resolution == "yes" else values[0]
        cat_records.append(
            {"id": post["id"], "type": qtype, "p": p, "score": sp, "k": 2}
        )
    elif qtype == "multiple_choice":
        options = q.get("options") or []
        if resolution in options and options.index(resolution) < len(values):
            p = values[options.index(resolution)]
            cat_records.append(
                {
                    "id": post["id"],
                    "type": qtype,
                    "p": p,
                    "score": sp,
                    "k": len(options),
                }
            )
    elif qtype in ("numeric", "discrete", "date") and len(values) > 2:
        try:
            truth = float(resolution)
        except (TypeError, ValueError):
            continue
        x = scale_to_x(truth, q.get("scaling") or {})
        if x is None:
            continue
        num_records.append(
            {"id": post["id"], "type": qtype, "cdf": values, "x": x, "score": sp}
        )

print(f"categorical records: {len(cat_records)}  numeric-family: {len(num_records)}")
actual_cat = sum(r["score"] for r in cat_records)
actual_num = sum(r["score"] for r in num_records)
print(
    f"actual totals -- categorical {actual_cat:.2f}  numeric {actual_num:.2f}  "
    f"all {actual_cat + actual_num:.2f}"
)

binaries = [r for r in cat_records if r["type"] == "binary"]
mcs = [r for r in cat_records if r["type"] == "multiple_choice"]
actual_bin = sum(r["score"] for r in binaries)
actual_mc = sum(r["score"] for r in mcs)

print("\n=== clip floor sensitivity (BINARY only -- a clip is only implementable there) ===")
floors = [0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.49]
assert abs(cl.replay(binaries, 1e-9) - actual_bin) < 1e-6, "identity check failed"
curve = []
for lo in floors:
    total = cl.replay(binaries, lo)
    curve.append((lo, total))
    print(f"  floor={lo:<5} total={total:9.2f}  delta={total - actual_bin:+9.2f}")

best_floor, best_total = max(curve, key=lambda t: t[1])
print(f"  in-sample best floor={best_floor} total={best_total:.2f}")

# How much of our edge is direction rather than confidence: how often did we
# beat the peers at all, and what would ignoring our own view have scored?
beat = [r for r in cat_records if r["score"] > 0]
print(
    f"  questions where we beat the peers: {len(beat)}/{len(cat_records)} "
    f"(sum {sum(r['score'] for r in beat):+.2f})"
)

print("\n=== shrink toward uniform (all categorical, implementable for any k) ===")
assert abs(cl.replay_shrink(cat_records, 1.0) - actual_cat) < 1e-6, "identity check failed"
for w in (1.0, 0.9, 0.8, 0.6, 0.4, 0.2, 0.05):
    total = cl.replay_shrink(cat_records, w)
    print(f"  w={w:<5} total={total:9.2f}  delta={total - actual_cat:+9.2f}")

weights = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
loo_shrink = 0.0
wpicks = []
for i, rec in enumerate(cat_records):
    rest = cat_records[:i] + cat_records[i + 1 :]
    w_star = max(weights, key=lambda w: cl.replay_shrink(rest, w))
    wpicks.append(w_star)
    b = cl.peer_term(rec["p"], rec["score"])
    loo_shrink += cl.score_from_p(cl.shrink_to_uniform(rec["p"], rec["k"], w_star), b)
print(
    f"  LOO (weight chosen on the other n-1): total={loo_shrink:.2f} "
    f"vs actual {actual_cat:.2f} delta={loo_shrink - actual_cat:+.2f} "
    f"weights picked={sorted(set(wpicks))}"
)


loo_total = 0.0
picks = []
for i, rec in enumerate(binaries):
    rest = binaries[:i] + binaries[i + 1 :]
    lo_star = max(floors, key=lambda lo: cl.replay(rest, lo))
    picks.append(lo_star)
    b = cl.peer_term(rec["p"], rec["score"])
    loo_total += cl.score_from_p(cl.clip(rec["p"], lo_star), b)
print("\n=== leave-one-out (floor chosen on the other n-1 questions) ===")
print(
    f"  LOO total={loo_total:.2f}  vs actual binary {actual_bin:.2f}  "
    f"delta={loo_total - actual_bin:+.2f}"
)
print(f"  floors picked: {sorted(set(picks))}")

raw, clipped = cl.simulate_binary(binaries, best_floor, draws=10000, seed=20260902)
worse = sum(1 for t in raw if t <= actual_bin)
print(
    f"\n=== simulation: outcomes drawn from the peers "
    f"(binary only, n={len(binaries)}, 10000 draws) ==="
)
print(f"  actual binary total: {actual_bin:.2f}")
print(
    f"  null mean={sum(raw)/len(raw):.2f} p05={cl.quantile(raw, 0.05):.2f} "
    f"p50={cl.quantile(raw, 0.5):.2f} p95={cl.quantile(raw, 0.95):.2f}"
)
print(f"  P(total <= actual) = {worse/len(raw):.4f}")
print(
    f"  same draws with floor={best_floor}: mean={sum(clipped)/len(clipped):.2f} "
    f"p05={cl.quantile(clipped, 0.05):.2f} p95={cl.quantile(clipped, 0.95):.2f}"
)

print("
=== numeric-family: width x tail-floor grid ===")
print("  eps mixes a uniform over the range back in, so sharpening cannot send")
print("  the density at a tail outcome to zero")


def mixed_density(cdf, x, lam, eps):
    return (1.0 - eps) * density_at(rescale_cdf(cdf, lam), x) + eps * 1.0


def numeric_total(records, lam, eps):
    total = 0.0
    for r in records:
        b = cl.log2(density_at(r["cdf"], r["x"])) - r["score"] / cl.K
        total += cl.K * (cl.log2(mixed_density(r["cdf"], r["x"], lam, eps)) - b)
    return total


lams = [1.3, 1.0, 0.85, 0.7, 0.6, 0.5]
epss = [0.0, 0.02, 0.05, 0.10, 0.20]
header = "".join(f"{e:>10}" for e in epss)
print(f"  lam / eps {header}")
for lam in lams:
    cells = "".join(f"{numeric_total(num_records, lam, e):10.1f}" for e in epss)
    print(f"  {lam:<9}" + cells)

grid = [(lam, e) for lam in lams for e in epss]
best_ge = max(grid, key=lambda ge: numeric_total(num_records, *ge))
print(
    f"  in-sample best (lam, eps)={best_ge} "
    f"total={numeric_total(num_records, *best_ge):.2f} vs actual {actual_num:.2f}"
)

loo_num = 0.0
npicks = []
for i, rec in enumerate(num_records):
    rest = num_records[:i] + num_records[i + 1 :]
    ge = max(grid, key=lambda g: numeric_total(rest, *g))
    npicks.append(ge)
    b = cl.log2(density_at(rec["cdf"], rec["x"])) - rec["score"] / cl.K
    loo_num += cl.K * (cl.log2(mixed_density(rec["cdf"], rec["x"], *ge)) - b)
print(
    f"  LOO (grid chosen on the other n-1): total={loo_num:.2f} "
    f"vs actual {actual_num:.2f} delta={loo_num - actual_num:+.2f}"
)
print(f"  picks: {sorted(set(npicks))}")


print("\nDONE (read-only)")
