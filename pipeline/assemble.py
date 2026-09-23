"""Merge mesh + skeleton + weights + animations into rig.json. Loud validation, exit 1 on any error."""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path

from PIL import Image

EASE_NAMES = {"linear", "sineIn", "sineOut", "sineInOut", "quadIn", "quadOut", "quadInOut", "backOut"}
PROPS = {"rotation", "x", "y", "scaleX", "scaleY", "alpha"}


def fail(msg: str) -> None:
    print(f"assemble: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path):
    with open(path) as f:
        return json.load(f, parse_constant=lambda c: fail(f"non-finite number {c} in {path}"))


PATH_ROTATE_MODES = {"tangent", "chain", "chainscale"}
_PATH_KEYS = {"name", "bone", "points", "chain", "closed", "rotateMode",
              "position", "spacing", "rotateOffset", "rotateMix", "translateMix",
              "weighted"}


def load_skeleton(path):
    """skeleton.json is either a bare bone array (classic) or an object
    {"bones": [...], "paths": [...]} (path constraints v1). Returns
    (bones, paths); paths is [] for the array form."""
    doc = load_json(path)
    if isinstance(doc, list):
        return doc, []
    unknown = set(doc) - {"bones", "paths"}
    if unknown:
        fail(f"skeleton object form: unknown keys {sorted(unknown)} (want bones|paths)")
    if "bones" not in doc:
        fail("skeleton object form: missing 'bones'")
    return doc["bones"], doc.get("paths", [])


def validate_bones(bones, w, h):
    names = [b["name"] for b in bones]
    if len(set(names)) != len(names):
        fail("duplicate bone names")
    idx = {n: i for i, n in enumerate(names)}
    if sum(1 for b in bones if b["parent"] is None) != 1:
        fail("exactly one root bone required")
    for i, b in enumerate(bones):
        if b["parent"] is not None:
            if b["parent"] not in idx:
                fail(f"bone {b['name']}: unknown parent {b['parent']}")
            if idx[b["parent"]] >= i:
                fail(f"bones must be ordered parent-first ({b['name']})")
        for fld in ("x", "y", "rotation", "length"):
            if not math.isfinite(b[fld]):
                fail(f"bone {b['name']}: non-finite {fld}")
        if b["length"] < 0:
            fail(f"bone {b['name']}: negative length")
        if not (0 <= b["x"] <= w and 0 <= b["y"] <= h):
            fail(f"bone {b['name']} outside image bounds")


def validate_paths(paths, bones, w, h):
    """Loud validation of the optional skeleton `paths` section (path
    constraints v1, contract §16). Points are on-curve anchors in image px."""
    bone_names = {b["name"] for b in bones}
    bone_length = {b["name"]: b["length"] for b in bones}
    seen = set()
    for p in paths:
        name = p.get("name")
        if not isinstance(name, str) or not name:
            fail("path without a name")
        if name in seen:
            fail(f"duplicate path name {name!r}")
        seen.add(name)
        unknown = set(p) - _PATH_KEYS
        if unknown:
            fail(f"path {name}: unknown keys {sorted(unknown)}")
        if p.get("bone") not in bone_names:
            fail(f"path {name}: unknown owner bone {p.get('bone')!r}")
        chain = p.get("chain")
        if not isinstance(chain, list) or not chain:
            fail(f"path {name}: chain must be a non-empty bone list")
        if len(set(chain)) != len(chain):
            fail(f"path {name}: duplicate bone in chain")
        for b in chain:
            if b not in bone_names:
                fail(f"path {name}: unknown chain bone {b!r}")
            if bone_length[b] <= 0:
                fail(f"path {name}: chain bone {b!r} has length {bone_length[b]} — "
                     f"path spacing consumes each chain bone's length (contract §16d: "
                     f"spacingMode 'length' divides by it), so every chain bone needs "
                     f"length > 0")
        pts = p.get("points")
        if not isinstance(pts, list) or len(pts) < 2:
            fail(f"path {name}: points must list >=2 on-curve anchors")
        for pt in pts:
            if (not isinstance(pt, list) or len(pt) != 2
                    or any(not isinstance(c, (int, float)) or not math.isfinite(c) for c in pt)):
                fail(f"path {name}: malformed point {pt!r}")
            if not (0 <= pt[0] <= w and 0 <= pt[1] <= h):
                fail(f"path {name}: point {pt} outside image bounds")
        for i in range(len(pts) - 1):
            if math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) < 1e-6:
                fail(f"path {name}: consecutive points {i} and {i + 1} coincide "
                     f"(zero-length curve segment)")
        if p.get("closed"):
            fail(f"path {name}: closed paths are not supported in v1 "
                 f"(open curve geometry only)")
        if not isinstance(p.get("weighted", False), bool):
            fail(f"path {name}: weighted must be a boolean (true = one anchor "
                 f"bone per curve point, the curve can bend — contract §17)")
        if p.get("rotateMode", "chain") not in PATH_ROTATE_MODES:
            fail(f"path {name}: rotateMode must be one of {sorted(PATH_ROTATE_MODES)}")
        for fld in ("position", "spacing", "rotateOffset", "rotateMix", "translateMix"):
            v = p.get(fld, 0)
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                fail(f"path {name}: non-finite {fld}")


def validate_mesh(mesh):
    n = len(mesh["vertices"])
    if n == 0 or not mesh["triangles"]:
        fail("empty mesh")
    if n != len(mesh["uvs"]):
        fail("vertices/uvs length mismatch")
    for i, v in enumerate(mesh["vertices"]):
        if any(not math.isfinite(c) for c in v):
            fail(f"vertex {i}: non-finite coordinate")
    for i, uv in enumerate(mesh["uvs"]):
        if any(not math.isfinite(c) for c in uv):
            fail(f"uv {i}: non-finite coordinate")
    for tri in mesh["triangles"]:
        if any(not (0 <= i < n) for i in tri):
            fail(f"triangle index out of range: {tri}")


def validate_weights(weights, n_vertices, bones):
    if len(weights) != n_vertices:
        fail(f"weights ({len(weights)}) / vertices ({n_vertices}) length mismatch")
    for i, vw in enumerate(weights):
        if any(not math.isfinite(x) for x in vw["w"]):
            fail(f"vertex {i}: non-finite weight")
        s = sum(vw["w"])
        if abs(s - 1.0) > 0.01:
            fail(f"vertex {i} weights sum {s:.3f}, expected 1.0")
        if len(vw["bones"]) != len(vw["w"]) or len(vw["bones"]) > 4:
            fail(f"vertex {i}: bad influence list")
        for bi in vw["bones"]:
            if not (0 <= bi < len(bones)):
                fail(f"vertex {i}: bone index {bi} out of range")
            if not bones[bi].get("skin", True):
                fail(f"vertex {i}: weighted to non-skin bone {bones[bi]['name']}")


def validate_animations(anims, bones, paths=None):
    names = {b["name"] for b in bones}
    # Weighted paths (contract §17) generate one anchor bone per curve point at
    # WRITER time — accept them as track targets (both wave-compiler output and
    # hand-authored anchor keys). Anchors of non-weighted paths don't exist.
    for p in paths or []:
        if p.get("weighted"):
            names |= {f"{p['name']}_a{j}" for j in range(len(p["points"]))}
    root = next(b["name"] for b in bones if b["parent"] is None)
    for aname, a in anims.items():
        if not math.isfinite(a["duration"]):
            fail(f"{aname}: non-finite duration")
        if a["duration"] <= 0:
            fail(f"{aname}: duration must be > 0")
        seen = set()
        for tr in a["tracks"]:
            if "bone" not in tr:
                fail(f"{aname}: piece track {tr.get('piece', '?')!r} in a single-mesh "
                     f"build — display/alpha/order piece tracks need the multi-piece "
                     f"pipeline (--pieces, --target db)")
            if tr["bone"] not in names:
                fail(f"{aname}: unknown bone {tr['bone']}")
            if tr["prop"] not in PROPS:
                fail(f"{aname}: bad prop {tr['prop']}")
            if (tr["bone"], tr["prop"]) in seen:
                fail(f"{aname}: duplicate track for {tr['bone']}.{tr['prop']}")
            seen.add((tr["bone"], tr["prop"]))
            if tr["prop"] == "alpha" and tr["bone"] != root:
                fail(f"{aname}: alpha track allowed on root only")
            keys = tr["keys"]
            if not keys:
                fail(f"{aname}: empty keys on {tr['bone']}.{tr['prop']}")
            for k in keys:
                if not (math.isfinite(k["t"]) and math.isfinite(k["v"])):
                    fail(f"{aname}: non-finite key on {tr['bone']}.{tr['prop']}")
            ts = [k["t"] for k in keys]
            if any(t2 <= t1 for t1, t2 in zip(ts, ts[1:])):
                fail(f"{aname}: key times not strictly increasing on {tr['bone']}.{tr['prop']}")
            if ts[0] < 0 or ts[-1] > a["duration"]:
                fail(f"{aname}: key time out of [0, duration] on {tr['bone']}.{tr['prop']}")
            for k in keys:
                if "ease" in k and k["ease"] not in EASE_NAMES:
                    fail(f"{aname}: unknown ease {k['ease']}")
                if tr["prop"] in ("scaleX", "scaleY") and abs(k["v"]) < 0.2:
                    fail(f"{aname}: {tr['bone']}.{tr['prop']} key v={k['v']} is too close to zero "
                         f"(scale values are absolute factors (1.03 = +3%), not deltas)")
        if a.get("loop", True):
            for tr in a["tracks"]:
                keys = tr["keys"]
                if keys[0]["t"] == 0 and keys[-1]["t"] == a["duration"]:
                    v0, v1 = keys[0]["v"], keys[-1]["v"]
                    if abs(v0 - v1) > 1e-6:
                        fail(f"{aname}: {tr['bone']}.{tr['prop']} does not loop seamlessly "
                             f"(first key v={v0}, last key v={v1}) — "
                             f"seamless loop needs the final key to repeat the first key's value")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--skeleton", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--animations", required=True)
    ap.add_argument("--texture", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        mesh = load_json(args.mesh)
        bones, paths = load_skeleton(args.skeleton)
        if paths:
            fail("skeleton has a `paths` section: path constraints are supported only "
                 "by the DragonBones target (--target db); the legacy rig.json runtime "
                 "has no path solver")
        weights = load_json(args.weights)
        anims = load_json(args.animations)
        w, h = Image.open(args.texture).size
        if mesh["imageSize"] != [w, h]:
            fail(f"mesh imageSize {mesh['imageSize']} != texture size [{w}, {h}]")

        validate_bones(bones, w, h)
        validate_mesh(mesh)
        validate_weights(weights, len(mesh["vertices"]), bones)
        validate_animations(anims, bones)

        rig = {
            "version": 1,
            "texture": Path(args.texture).name,
            "imageSize": [w, h],
            "meshes": [{
                "vertices": mesh["vertices"], "uvs": mesh["uvs"],
                "triangles": mesh["triangles"], "weights": weights,
            }],
            "bones": bones,
            "animations": anims,
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rig))
        tex_dst = out.parent / Path(args.texture).name
        if tex_dst.resolve() != Path(args.texture).resolve():
            shutil.copy(args.texture, tex_dst)
        print(f"assemble: {len(bones)} bones, {len(mesh['vertices'])} vertices, "
              f"{len(mesh['triangles'])} tris, animations: {', '.join(anims)} -> {out}")
    except (KeyError, TypeError) as e:
        fail(f"malformed input: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
