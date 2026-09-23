"""Vertex weights over skin bones. Two algorithms sharing one finish
(top-4 influences, cutoff, normalize):

- idw: w = 1/(straight-line dist to bone segment)^p. Fast, but influence
  crosses empty space and narrow junctions (raised arm drags the torso).
- heat: one implicit heat-diffusion step on the alpha-mask pixel grid —
  influence travels through painted pixels only.
"""
import argparse
import json
import math
import sys
from pathlib import Path

# Reuse assemble.py's skeleton loader (object-form skeleton.json support).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble  # noqa: E402
from mesh_gen import ALPHA_THRESHOLD  # noqa: E402  # one threshold for meshing AND weighting


def dist_point_seg(px, py, ax, ay, bx, by):
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    t = 0.0 if ab2 == 0 else max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
    return math.hypot(px - (ax + t * abx), py - (ay + t * aby))


def bone_segment(b):
    rot = math.radians(b.get("rotation", 0.0))
    length = b.get("length", 0.0)
    return b["x"], b["y"], b["x"] + length * math.cos(rot), b["y"] + length * math.sin(rot)


def _select_skin(bones, only_bones):
    """Skin-bone influence pool: (index, bone) pairs. `only_bones` (a set of
    NAMES) restricts the pool; unknown / non-skin names raise ValueError."""
    skin = [(i, b) for i, b in enumerate(bones) if b.get("skin", True)]
    if only_bones is not None:
        skin_names = {b["name"] for _, b in skin}
        bad = set(only_bones) - skin_names
        if bad:
            raise ValueError(f"only_bones names not skin bones: {sorted(bad)}")
        skin = [(i, b) for i, b in skin if b["name"] in only_bones]
    if not skin:
        raise ValueError("no skin bones")
    return skin


def _finish(raw, cutoff, max_influences):
    """raw = [(bone_index, strength)] for ONE vertex -> {"bones", "w"}."""
    raw = sorted(raw, key=lambda t: -t[1])[:max_influences]
    s = sum(w for _, w in raw)
    raw = [(i, w / s) for i, w in raw]
    raw = [(i, w) for i, w in raw if w >= cutoff] or raw[:1]
    s = sum(w for _, w in raw)
    return {"bones": [i for i, _ in raw], "w": [round(w / s, 4) for _, w in raw]}


def _idw_raw(vx, vy, segs, p):
    return [(i, 1.0 / ((dist_point_seg(vx, vy, ax, ay, bx, by) + 1e-6) ** p))
            for i, ax, ay, bx, by in segs]


def compute_weights(vertices, bones, p=4.0, cutoff=0.05, max_influences=4, only_bones=None):
    """Inverse-distance weights over the skin bones."""
    skin = _select_skin(bones, only_bones)
    segs = [(i, *bone_segment(b)) for i, b in skin]
    return [_finish(_idw_raw(vx, vy, segs, p), cutoff, max_influences)
            for vx, vy in vertices]


def _downsample_mask(alpha, heat_res):
    """Boolean occupancy grid, block-reduced with any() so 1px strands stay
    connected. Tradeoff: gaps narrower than `scale` image px can be bridged
    by the any() reduce, re-enabling cross-gap leaks — the deliberate price
    of keeping thin strands alive; raise `heat_res` if separation must be
    preserved. Returns (mask, scale): grid cell (r, c) covers image pixels
    [r*scale, (r+1)*scale) x [c*scale, (c+1)*scale)."""
    import numpy as np
    alpha = np.asarray(alpha)
    h, w = alpha.shape
    scale = max(1, -(-max(h, w) // heat_res))
    ph, pw = -(-h // scale) * scale, -(-w // scale) * scale
    padded = np.zeros((ph, pw), dtype=bool)
    padded[:h, :w] = alpha > ALPHA_THRESHOLD
    return padded.reshape(ph // scale, scale, pw // scale, scale).any(axis=(1, 3)), scale


def _raster_segment(ax, ay, bx, by, rows, cols):
    """Grid cells (r, c) touched by a segment given in GRID coordinates.
    The 0.5-cell sampling can leave an 8-connected (diagonal) seed trail —
    harmless: seeds only initialize a field that diffuses over the true
    4-connected graph."""
    steps = max(1, int(math.hypot(bx - ax, by - ay) * 2))
    cells = set()
    for s in range(steps + 1):
        t = s / steps
        c = min(cols - 1, max(0, int(ax + t * (bx - ax))))
        r = min(rows - 1, max(0, int(ay + t * (by - ay))))
        cells.add((r, c))
    return cells


def compute_weights_heat(vertices, bones, alpha, heat_res=256, heat_time=None,
                         cutoff=0.05, max_influences=4, only_bones=None):
    """Heat weights: rasterize each skin bone into the (downsampled) alpha
    mask and solve one implicit diffusion step (I + t*L)u = source on the
    masked-cell graph Laplacian (4-connectivity). One sparse factorization
    serves every bone. Vertices outside the mask (mesh dilation margin)
    sample the nearest masked cell. Mask islands with no bone source fall
    back to IDW for their vertices (loud warning)."""
    import numpy as np
    from scipy import ndimage, sparse
    from scipy.sparse.linalg import splu

    skin = _select_skin(bones, only_bones)
    mask, scale = _downsample_mask(alpha, heat_res)
    rows, cols = mask.shape
    n = int(mask.sum())
    if n == 0:
        raise ValueError("image is fully transparent")

    node = np.full(mask.shape, -1, dtype=np.int64)
    node[mask] = np.arange(n)

    ii, jj = [], []
    for dr, dc in ((0, 1), (1, 0)):
        both = mask[:rows - dr, :cols - dc] & mask[dr:, dc:]
        ii.append(node[:rows - dr, :cols - dc][both])
        jj.append(node[dr:, dc:][both])
    ii, jj = np.concatenate(ii), np.concatenate(jj)
    adj = sparse.coo_matrix(
        (np.ones(2 * len(ii)), (np.concatenate([ii, jj]), np.concatenate([jj, ii]))),
        shape=(n, n)).tocsr()
    lap = sparse.diags(np.asarray(adj.sum(axis=1)).ravel()) - adj

    if heat_time is None:
        # Diffusion length ~= diagonal/40 cells. Larger divisors keep influence
        # local; the original diagonal/10 was near steady-state (harmonic) and
        # flooded a broad thin arm share across the whole connected torso,
        # LOSING to IDW on the man.png gate (heat 4.59 vs idw 2.97 arm-mass in
        # the torso probe box). /40 concentrates heat near each bone: same
        # probe box drops to heat 1.07 (ratio 0.36); the gate plateaus past
        # ~40, and higher divisors only sharpen influence on small pieces for
        # no gain — sharper is not strictly better. See test_man_gate.py.
        heat_time = (math.hypot(rows, cols) / 40.0) ** 2
    solver = splu((sparse.identity(n, format="csr") + heat_time * lap).tocsc())

    # Nearest masked cell, for clamping bone sources and margin vertices.
    _, (near_r, near_c) = ndimage.distance_transform_edt(~mask, return_indices=True)

    # 4-connected components (ndimage.label default structure), to tell true
    # bare islands apart from mere numerical heat underflow.
    labels, _ = ndimage.label(mask)
    sourced = set()  # component labels that received >=1 bone source cell

    # Total injected heat scales with bone length (more source cells) —
    # intentional: longer bones legitimately dominate at equal distance,
    # mirroring IDW's segment-distance behavior.
    fields = np.empty((n, len(skin)))
    for k, (_, b) in enumerate(skin):
        ax, ay, bx, by = bone_segment(b)
        src = np.zeros(n)
        clamped = 0
        for r, c in _raster_segment(ax / scale, ay / scale, bx / scale, by / scale,
                                    rows, cols):
            if not mask[r, c]:
                clamped += 1
                r, c = near_r[r, c], near_c[r, c]
            src[node[r, c]] = 1.0
            sourced.add(int(labels[r, c]))
        if clamped:
            print(f"weights_gen: WARNING: bone '{b['name']}': {clamped} source "
                  f"cell(s) outside the silhouette (joints belong inside) — "
                  f"clamped to the nearest masked cell", file=sys.stderr)
        fields[:, k] = solver.solve(src)
    fields = np.maximum(fields, 0.0)   # scrub LU numerical noise
    totals = fields.sum(axis=1)

    segs = [(i, *bone_segment(b)) for i, b in skin]  # island IDW fallback pool
    out, islands, underflows = [], 0, 0
    for vx, vy in vertices:
        r = min(rows - 1, max(0, int(vy / scale)))
        c = min(cols - 1, max(0, int(vx / scale)))
        if not mask[r, c]:
            r, c = near_r[r, c], near_c[r, c]
        nd = node[r, c]
        if int(labels[r, c]) not in sourced:
            islands += 1
            out.append(_finish(_idw_raw(vx, vy, segs, 4.0), cutoff, max_influences))
            continue
        if totals[nd] <= 0.0:
            # Connected to a source, but the solution decayed below float
            # range (tiny heat_time on a long snaky mask). Also keeps
            # _finish from an empty raw list.
            underflows += 1
            out.append(_finish(_idw_raw(vx, vy, segs, 4.0), cutoff, max_influences))
            continue
        raw = [(skin[k][0], float(fields[nd, k])) for k in range(len(skin))
               if fields[nd, k] > 0.0]
        out.append(_finish(raw, cutoff, max_influences))
    if islands:
        print(f"weights_gen: WARNING: {islands} vertex(es) on mask islands with "
              f"no bone source — fell back to IDW for those", file=sys.stderr)
    if underflows:
        print(f"weights_gen: WARNING: heat underflowed at {underflows} vertex(es) "
              f"— fell back to IDW for those (a larger --heat-time diffuses "
              f"further and avoids this)", file=sys.stderr)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mesh")
    ap.add_argument("skeleton")
    ap.add_argument("out")
    ap.add_argument("--power", type=float, default=4.0, help="IDW falloff exponent")
    ap.add_argument("--algo", default="heat", choices=["idw", "heat"])
    ap.add_argument("--image", help="source image (alpha mask; required for --algo heat)")
    ap.add_argument("--heat-time", type=float, default=None,
                    help="diffusion scale; default derived from the mask diagonal")
    ap.add_argument("--heat-res", type=int, default=256,
                    help="heat grid resolution (mask long side)")
    args = ap.parse_args(argv)
    with open(args.mesh) as f:
        mesh = json.load(f)
    bones, _paths = assemble.load_skeleton(args.skeleton)
    try:
        if args.algo == "heat":
            if not args.image:
                raise ValueError("--algo heat requires --image (the alpha-mask source)")
            import numpy as np
            from PIL import Image
            alpha = np.array(Image.open(args.image).convert("RGBA"))[:, :, 3]
            weights = compute_weights_heat(mesh["vertices"], bones, alpha,
                                           heat_res=args.heat_res,
                                           heat_time=args.heat_time)
        else:
            weights = compute_weights(mesh["vertices"], bones, p=args.power)
    except ValueError as e:
        raise SystemExit(f"weights_gen: {e}")
    with open(args.out, "w") as f:
        json.dump(weights, f)
    print(f"weights_gen: {len(weights)} vertices weighted (algo={args.algo}) -> {args.out}")


if __name__ == "__main__":
    main()
