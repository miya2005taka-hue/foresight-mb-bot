"""Pure calibration maths for the counterfactual replay. No network, no deps.

Metaculus spot peer score on the realised outcome behaves as

    score = K * (log2(p_ours) - B)

where B is the peers' mean log2 probability on that same outcome and K = 100.
Given our submitted probability and the score we actually received, B is
recoverable, which lets us re-score counterfactual probabilities exactly.
"""

import math
import random

K = 100.0
EPS = 1e-12


def log2(p):
    return math.log(max(p, EPS), 2)


def peer_term(p_ours, observed_score, k=K):
    """Recover the peers' mean log2 probability on the realised outcome."""
    return log2(p_ours) - observed_score / k


def score_from_p(p, b, k=K):
    """Score we would have received had we submitted p on the realised outcome."""
    return k * (log2(p) - b)


def clip(p, lo):
    """Symmetric clip of a probability into [lo, 1 - lo]."""
    if not 0.0 < lo < 0.5:
        raise ValueError("clip floor must be in (0, 0.5)")
    return min(max(p, lo), 1.0 - lo)


def replay(records, lo):
    """Total score over records if every probability had been clipped at lo.

    Each record: {"p": submitted probability on the realised outcome,
                  "score": the score actually received}.
    """
    total = 0.0
    for r in records:
        b = peer_term(r["p"], r["score"])
        total += score_from_p(clip(r["p"], lo), b)
    return total


def simulate_binary(records, lo, draws, seed):
    """Null distribution of our total under peer-generated outcomes.

    For binary questions the peers' probability on the realised outcome is
    recoverable, so the complementary outcome's peer probability is known too.
    Treating the peers as calibrated, outcomes are drawn from them and both the
    unclipped and clipped totals are recomputed. Returns (raw_totals,
    clipped_totals).
    """
    rng = random.Random(seed)
    raw_totals, clipped_totals = [], []
    prepared = []
    for r in records:
        b = peer_term(r["p"], r["score"])
        p_peer = 2.0**b  # peer probability on the outcome that happened
        prepared.append((r["p"], p_peer))
    for _ in range(draws):
        raw = clipped = 0.0
        for p_ours, p_peer in prepared:
            # Draw whether the outcome we forecast at p_ours occurs.
            happened = rng.random() < p_peer
            p_out = p_ours if happened else 1.0 - p_ours
            p_peer_out = p_peer if happened else 1.0 - p_peer
            b_out = log2(p_peer_out)
            raw += score_from_p(p_out, b_out)
            clipped += score_from_p(clip(p_out, lo), b_out)
        raw_totals.append(raw)
        clipped_totals.append(clipped)
    return raw_totals, clipped_totals


def quantile(values, q):
    if not values:
        return None
    s = sorted(values)
    pos = q * (len(s) - 1)
    i = int(pos)
    if i >= len(s) - 1:
        return s[-1]
    return s[i] + (s[i + 1] - s[i]) * (pos - i)


def shrink_to_uniform(p, k, w):
    """Shrink one option's probability toward a uniform 1/k with weight w.

    Unlike a hard clip this stays implementable for k>2 options: every option
    is shrunk by the same rule, so the vector still sums to one.
    """
    if k < 2:
        raise ValueError("k must be at least 2")
    if not 0.0 < w <= 1.0:
        raise ValueError("shrink weight must be in (0, 1]")
    return w * p + (1.0 - w) / k


def replay_shrink(records, w):
    """Total score if every categorical forecast had been shrunk toward uniform."""
    total = 0.0
    for r in records:
        b = peer_term(r["p"], r["score"])
        total += score_from_p(shrink_to_uniform(r["p"], r["k"], w), b)
    return total
