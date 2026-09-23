"""Path curve math shared by the DragonBones writer and the wave compiler.

Single source for the Catmull-Rom -> cubic-bezier conversion, arc lengths and
per-point tangent frames, so anchor rest rotations (dragonbones_writer
.build_path_elements, contract §17) and wave-compiler offsets (wave_compiler
.expand_wave_tracks) can never drift apart.
"""
import math


def catmull_rom_to_bezier(points):
    """Clamped-ends Catmull-Rom spline through `points` (on-curve anchors,
    world/image px) -> cubic bezier segments `[P0, C1, C2, P3]`, one per pair
    of consecutive anchors (N anchors -> N-1 segments)."""
    pts = [points[0]] + list(points) + [points[-1]]
    segs = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        c1 = [p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0]
        c2 = [p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0]
        segs.append([list(p1), c1, c2, list(p2)])
    return segs


def _bezier_point(p0, c1, c2, p3, t):
    mt = 1.0 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    x = a * p0[0] + b * c1[0] + c * c2[0] + d * p3[0]
    y = a * p0[1] + b * c1[1] + c * c2[1] + d * p3[1]
    return x, y


def bezier_arc_lengths(segs, samples=64, rounder=None):
    """Per-segment arc length (64-point polyline sample, contract §16b), then
    CUMULATIVE from the curve start — entry k = length of curve start..end of
    segment k. This is what DragonBones' `lengths`/`curveLengths` holds.
    `rounder` (if given) is applied to each cumulative entry — the writer
    passes its output-rounding convention."""
    lengths = []
    total = 0.0
    for p0, c1, c2, p3 in segs:
        prev = _bezier_point(p0, c1, c2, p3, 0.0)
        seg_len = 0.0
        for i in range(1, samples):
            t = i / (samples - 1)
            pt = _bezier_point(p0, c1, c2, p3, t)
            seg_len += math.hypot(pt[0] - prev[0], pt[1] - prev[1])
            prev = pt
        total += seg_len
        lengths.append(rounder(total) if rounder else total)
    return lengths


def path_anchor_frames(points):
    """Per on-curve point j: `(tangent_degrees, u)` — the local curve tangent
    angle (degrees, y-down, -90 = up; from the Catmull-Rom handle chord
    hOut - hIn, ends use their single handle) and the normalized rest arc
    position u in [0, 1]. Used for §17 anchor rest rotations and for wave
    phase/envelope sampling."""
    n = len(points)
    segs = catmull_rom_to_bezier(points)
    lengths = bezier_arc_lengths(segs)
    frames = []
    for j in range(n):
        pt = points[j]
        t_from = segs[j - 1][2] if j > 0 else pt          # hIn_j (ends: pt)
        t_to = segs[j][1] if j < n - 1 else pt            # hOut_j (ends: pt)
        if list(t_to) == list(t_from):                    # degenerate guard
            t_from, t_to = points[max(j - 1, 0)], points[min(j + 1, n - 1)]
        deg = math.degrees(math.atan2(t_to[1] - t_from[1], t_to[0] - t_from[0]))
        u = 0.0 if j == 0 else lengths[j - 1] / lengths[-1]
        frames.append((deg, u))
    return frames
