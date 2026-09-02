"""Gate for calib_lib: the replay maths must invert the observed scores exactly."""

import sys

sys.path.insert(0, "scripts")
import calib_lib as cl

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS {label}")
    else:
        failed += 1
        print(f"FAIL {label}")


# B1: peer_term inverts score_from_p exactly.
b = cl.peer_term(0.03, -293.05)
check("B1 peer term round-trips the observed score", abs(cl.score_from_p(0.03, b) - (-293.05)) < 1e-9)

# B2: recovered peer probability is a probability, and matches the worked example.
p_peer = 2.0**b
check("B2 recovered peer probability in (0,1)", 0.0 < p_peer < 1.0)
check("B2 worked example ~0.228", abs(p_peer - 0.228) < 0.01)

# B3: a clip that does not bind leaves the score untouched.
recs = [{"p": 0.42, "score": 12.0}, {"p": 0.60, "score": -3.0}]
check("B3 non-binding clip is a no-op", abs(cl.replay(recs, 0.05) - 9.0) < 1e-9)

# B4: clipping a confident-and-wrong forecast strictly improves it.
bad = [{"p": 0.03, "score": -293.05}]
check("B4 clipping raises a badly wrong forecast", cl.replay(bad, 0.10) > -293.05)

# B5: clipping a confident-and-right forecast strictly costs something.
good = [{"p": 0.99, "score": 61.81}]
check("B5 clipping costs on a confident hit", cl.replay(good, 0.10) < 61.81)

# B6: the replay is monotone in the clip floor for a wrong-side forecast.
totals = [cl.replay(bad, lo) for lo in (0.02, 0.05, 0.10, 0.20)]
check("B6 monotone improvement as the floor rises", totals == sorted(totals))

# B7: an out-of-range floor is rejected rather than silently accepted.
try:
    cl.clip(0.5, 0.5)
    check("B7 invalid floor rejected", False)
except ValueError:
    check("B7 invalid floor rejected", True)

# B8: the simulation is deterministic under a fixed seed and returns the asked size.
raw1, clip1 = cl.simulate_binary(bad, 0.10, 200, seed=7)
raw2, clip2 = cl.simulate_binary(bad, 0.10, 200, seed=7)
check("B8 simulation deterministic under seed", raw1 == raw2 and clip1 == clip2)
check("B8 simulation returns requested draws", len(raw1) == 200)

# B9: a forecaster who matches the peers scores ~0 on average.
same = [{"p": 0.30, "score": 0.0}]
raw, _ = cl.simulate_binary(same, 0.05, 4000, seed=11)
check("B9 matching the peers averages ~0", abs(sum(raw) / len(raw)) < 5.0)

# B10: quantiles are ordered.
qs = [cl.quantile(raw, q) for q in (0.05, 0.5, 0.95)]
check("B10 quantiles ordered", qs == sorted(qs))


# B11: shrinking with w=1 is the identity, so the replay reproduces the actuals.
recs_k = [{"p": 0.03, "score": -293.05, "k": 2}, {"p": 0.65, "score": -17.27, "k": 3}]
check("B11 w=1 reproduces the observed total", abs(cl.replay_shrink(recs_k, 1.0) - (-310.32)) < 1e-6)

# B12: shrinking pulls toward 1/k from either side.
check("B12 shrink raises a low probability", cl.shrink_to_uniform(0.03, 2, 0.5) > 0.03)
check("B12 shrink lowers a high probability", cl.shrink_to_uniform(0.90, 2, 0.5) < 0.90)
check("B12 shrink lands on 1/k as w->0", abs(cl.shrink_to_uniform(0.9, 4, 1e-9) - 0.25) < 1e-6)

# B13: a clip floor of ~0 is the identity too.
check("B13 floor 1e-9 reproduces the observed total", abs(cl.replay(recs, 1e-9) - 9.0) < 1e-9)

# B14: invalid shrink weights are rejected.
try:
    cl.shrink_to_uniform(0.5, 2, 0.0)
    check("B14 invalid shrink weight rejected", False)
except ValueError:
    check("B14 invalid shrink weight rejected", True)

print(f"\n{'ALL PASS' if not failed else 'FAILURES'}: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
