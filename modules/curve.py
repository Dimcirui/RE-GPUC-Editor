"""A config curve's value (via.AnimationCurve, an approximation): keys (time, value, type, in tangent, out
tangent) in time order; between a key and the next, type 1 linear, 2 step (the key's value held), any other a
cubic Hermite by the key's out tangent and the next key's in tangent (slopes in value per time; the weighted
types' weights are not used). Before the first key and after the last the end values hold."""
from __future__ import annotations

import numpy as np

LINEAR, STEP = 1, 2


def evaluate(keys, x, clamp=None) -> np.ndarray:
    """keys: (time, value, type, in tangent, out tangent) each; x: a number or an array; clamp: (min, max) or
    None."""
    x = np.asarray(x, float)
    if not len(keys):
        return np.zeros_like(x)
    k = np.asarray(keys, float).reshape(-1, 5)
    t, v, typ, tin, tout = k.T
    if len(k) == 1:
        y = np.full_like(x, v[0])
    else:
        i = np.clip(np.searchsorted(t, x, side="right") - 1, 0, len(k) - 2)
        t0, t1 = t[i], t[i + 1]
        dt = np.maximum(t1 - t0, 1e-9)
        s = np.clip((x - t0) / dt, 0.0, 1.0)
        v0, v1 = v[i], v[i + 1]
        s2, s3 = s * s, s * s * s
        hermite = ((2 * s3 - 3 * s2 + 1) * v0 + (s3 - 2 * s2 + s) * dt * tout[i]
                   + (-2 * s3 + 3 * s2) * v1 + (s3 - s2) * dt * tin[i + 1])
        y = np.where(typ[i] == LINEAR, v0 + (v1 - v0) * s, np.where(typ[i] == STEP, v0, hermite))
        y = np.where(x <= t[0], v[0], np.where(x >= t[-1], v[-1], y))
    if clamp is not None:
        y = np.clip(y, clamp[0], clamp[1])
    return y
