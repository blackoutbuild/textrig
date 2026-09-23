"""PNG alpha -> regular grid mesh, empty cells culled (1-cell dilation margin)."""
import argparse
import json

import numpy as np
from PIL import Image

ALPHA_THRESHOLD = 8


def build_mesh(alpha: np.ndarray, target_cols: int = 16) -> dict:
    h, w = alpha.shape
    ys, xs = np.nonzero(alpha > ALPHA_THRESHOLD)
    if len(xs) == 0:
        raise ValueError("image is fully transparent")
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    cell = max(1, -(-(x1 - x0) // target_cols))  # ceil div, from tight bbox width
    # Pad bbox by one cell so the dilation margin can extend OUTWARD past the
    # silhouette (glow/halo pixels with alpha <= threshold sit just outside it).
    x0, y0 = max(0, x0 - cell), max(0, y0 - cell)
    x1, y1 = min(w, x1 + cell), min(h, y1 + cell)
    cols = -(-(x1 - x0) // cell)
    rows = -(-(y1 - y0) // cell)

    occ = np.zeros((rows, cols), bool)
    for r in range(rows):
        for c in range(cols):
            by, bx = y0 + r * cell, x0 + c * cell
            block = alpha[by:min(by + cell, h), bx:min(bx + cell, w)]
            occ[r, c] = bool((block > ALPHA_THRESHOLD).any())

    d = occ.copy()
    d[1:, :] |= occ[:-1, :]
    d[:-1, :] |= occ[1:, :]
    d[:, 1:] |= occ[:, :-1]
    d[:, :-1] |= occ[:, 1:]
    occ = d

    vid = {}
    vertices = []
    uvs = []
    triangles = []

    def vert(r, c):
        key = (r, c)
        if key not in vid:
            x = min(x0 + c * cell, w)
            y = min(y0 + r * cell, h)
            vid[key] = len(vertices)
            vertices.append([int(x), int(y)])
            uvs.append([round(x / w, 5), round(y / h, 5)])
        return vid[key]

    for r in range(rows):
        for c in range(cols):
            if not occ[r, c]:
                continue
            tl, tr = vert(r, c), vert(r, c + 1)
            bl, br = vert(r + 1, c), vert(r + 1, c + 1)
            triangles.append([tl, tr, bl])
            triangles.append([tr, br, bl])

    return {"imageSize": [int(w), int(h)], "vertices": vertices, "uvs": uvs, "triangles": triangles}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--cols", type=int, default=16)
    args = ap.parse_args()
    alpha = np.array(Image.open(args.image).convert("RGBA"))[:, :, 3]
    try:
        mesh = build_mesh(alpha, target_cols=args.cols)
    except ValueError as e:
        raise SystemExit(f"mesh_gen: {e}")
    with open(args.out, "w") as f:
        json.dump(mesh, f)
    print(f"mesh_gen: {len(mesh['vertices'])} vertices, {len(mesh['triangles'])} triangles -> {args.out}")


if __name__ == "__main__":
    main()
