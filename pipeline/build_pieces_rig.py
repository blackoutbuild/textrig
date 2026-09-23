"""Orchestrator for the multi-piece pipeline: a source PNG +
skeleton.json + pieces.json + animations.json -> DragonBones `<name>_ske.json`
+ `<name>_tex.json` + `<name>_tex.png`, plus debug aids.

Wires together (do NOT modify the wired modules — their exact signatures are
the contract): normalize_input (alpha) -> assemble/pieces (loud validation) ->
cut_pieces (cut) -> fill_occlusions (repaint) -> pack_atlas (compose) ->
dragonbones_writer (serialize) -> debug_viz (debug PNG + cutouts).

deform pieces are supported: each gets a per-piece mesh (mesh_gen) + IDW or
heat-diffusion weights (weights_gen, --weights-algo) populated between
fill_occlusions and the atlas flatten, then serialized by
dragonbones_writer._deform_display as a weighted mesh display.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble  # noqa: E402
import cut_pieces  # noqa: E402
import debug_viz  # noqa: E402
import fill_occlusions  # noqa: E402
import mesh_gen  # noqa: E402
import morph_compiler  # noqa: E402
import normalize_input  # noqa: E402
import pack_atlas  # noqa: E402
import pieces  # noqa: E402
import wave_compiler  # noqa: E402
import dragonbones_writer  # noqa: E402
from dragonbones_writer import _run_or_fail, build_ske_multipiece, build_tex_multi  # noqa: E402
from weights_gen import compute_weights, compute_weights_heat  # noqa: E402


def fail(msg: str) -> None:
    print(f"build_pieces_rig: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# Rebind every merged module's loud-failure hook so error output says which
# tool is running (same pattern dragonbones_writer.py uses for assemble.fail).
pieces.fail = fail
cut_pieces.fail = fail
normalize_input.fail = fail
pack_atlas.fail = fail
assemble.fail = fail
dragonbones_writer.fail = fail
morph_compiler.fail = fail


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--skeleton", required=True)
    ap.add_argument("--pieces", required=True)
    ap.add_argument("--animations", required=True)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", required=True, help="asset base name (<name>_ske.json …)")
    ap.add_argument("--bg", default="auto", choices=["auto", "rembg"])
    ap.add_argument("--bg-tolerance", type=int, default=24)
    ap.add_argument("--atlas-max-width", type=int, default=2048)
    ap.add_argument("--fill-blur", type=float, default=3.0)
    ap.add_argument("--underlap", type=int, default=30,
                    help="px each piece dives under the pieces above it (0 = off)")
    ap.add_argument("--fill-mode", default="auto", choices=["auto", "extend", "inpaint"])
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--weights-algo", default="heat", choices=["idw", "heat"])
    args = ap.parse_args(argv)

    def body():
        bones, paths = assemble.load_skeleton(args.skeleton)
        anims = assemble.load_json(args.animations)
        img = Image.open(args.image)
        rgba = normalize_input.ensure_alpha(img, bg=args.bg, tolerance=args.bg_tolerance)
        src_rgba = np.array(rgba)
        h, w = src_rgba.shape[:2]

        assemble.validate_bones(bones, w, h)
        assemble.validate_paths(paths, bones, w, h)
        pieces_list = pieces.load_pieces(args.pieces, bones, (w, h), deform_allowed=True)
        anims = wave_compiler.expand_wave_tracks(anims, paths, bones, fps=args.fps)
        bone_anims, piece_tracks = pieces.split_tracks(anims)
        assemble.validate_animations(bone_anims, bones, paths=paths)
        pieces.validate_piece_tracks(anims, pieces_list)

        # Morph pieces carry whole keyframe images (not a polygon/layer to cut
        # or repaint) — pull them out before cut_pieces/fill_occlusions, which
        # must never see them.
        morph_pieces = [p for p in pieces_list if p["type"] == "morph"]
        cut_pieces_list = [p for p in pieces_list if p["type"] != "morph"]

        built = cut_pieces.cut_all(src_rgba, cut_pieces_list, underlap=args.underlap)
        fill_occlusions.fill_occlusions(built, blur_sigma=args.fill_blur, mode=args.fill_mode)

        # Deform pieces: build a per-piece mesh over the cutout (LOCAL vertices +
        # region-relative uvs) and IDW/heat weights (weights_gen) — idw measures
        # against WORLD-space vertices, heat runs in the piece-local frame (see
        # the branch below); bone indices are global either way. The writer's
        # _deform_display re-applies the piece offset, so mesh_data carries
        # LOCAL vertices.
        for p in built:
            if p["type"] != "deform":
                continue
            knobs = p.get("mesh", {})
            try:
                mesh = mesh_gen.build_mesh(p["img"][:, :, 3],
                                           target_cols=knobs.get("cols", 16))
                ox, oy = p["offset"]
                only = set(p["bones"]) if p.get("bones") else None
                if args.weights_algo == "heat":
                    # Heat runs in the PIECE frame: local mesh vertices, the
                    # cutout's own alpha, bones shifted by -offset. Output
                    # bone indices are global either way.
                    local_bones = [dict(b, x=b["x"] - ox, y=b["y"] - oy) for b in bones]
                    weights = compute_weights_heat(mesh["vertices"], local_bones,
                                                   p["img"][:, :, 3], only_bones=only)
                else:
                    world_vertices = [[x + ox, y + oy] for x, y in mesh["vertices"]]
                    weights = compute_weights(world_vertices, bones,
                                              p=knobs.get("power", 4.0), only_bones=only)
            except ValueError as e:
                raise ValueError(f"piece {p['name']}: {e}") from e
            p["mesh_data"] = mesh
            p["weights_data"] = weights

        # Morph pieces: expand each into ONE morphframe piece carrying K mesh
        # displays + an FFD timeline per display + a stepped display track
        # (morph_compiler.expand_morph), all living on the single animation the
        # MVP constraint requires. The morphframe piece joins `built` here —
        # AFTER cut_all/fill_occlusions ran on the normal pieces — so its own
        # `img`/variant imgs never go through either step (they're already-final
        # whole-canvas key frames); guide frames never join anything, they exist
        # only to steer the flow field.
        ffd_timelines = []
        morph_summary = []
        for p in morph_pieces:
            frame_images = []
            for entry in p["frames"]:
                arr = np.array(Image.open(entry["path"]).convert("RGBA"))
                fh, fw = arr.shape[:2]
                if (fw, fh) != (w, h):
                    fail(f"piece {p['name']}: frame {entry['path']} is {fw}x{fh}, "
                         f"canvas is {w}x{h} — morph frames must share the source "
                         f"canvas size")
                frame_images.append(arr)
            p["frame_images"] = frame_images
            # The morph slot is parented to its owner bone (default root); the
            # synthesized global-motion track lands on that bone.
            owner_name = p.get("bone") or bones[0]["name"]
            fp, ffd, display_tracks, bone_translation = morph_compiler.expand_morph(
                p, anims, fps=args.fps, owner_bone_name=p.get("bone"))
            built.extend(fp)
            ffd_timelines.extend(ffd)
            (aname, _), = anims.items()  # expand_morph already enforced len==1
            piece_tracks[aname] = piece_tracks.get(aname, []) + display_tracks
            if bone_translation:
                # Rotate the image-space global into the owner bone's PARENT
                # frame (bone translate deltas live there) and fold x/y tracks
                # into the bone animation. Median-decomposed global motion now
                # rides the bone exactly; the mesh FFD carries only the residual.
                keys = bone_translation["keys"]
                xk, yk = [], []
                for t, gx, gy in keys:
                    lx, ly = dragonbones_writer.image_delta_to_bone_local(
                        bones, owner_name, gx, gy)
                    xk.append({"t": t, "v": lx})
                    yk.append({"t": t, "v": ly})
                bone_anims[aname]["tracks"].extend([
                    {"bone": owner_name, "prop": "x", "keys": xk},
                    {"bone": owner_name, "prop": "y", "keys": yk}])
            n_keys = 1 + len(fp[0]["variants"])   # display 0 + K-1 variants
            morph_summary.append(f"{p['name']} x{n_keys} keys")

        if morph_pieces:
            # Re-validate bone tracks (author + synthesized global-motion) and the
            # COMBINED piece-track set (author + synthesized display tracks)
            # against the EXPANDED piece list (frame pieces stand in for the morph
            # piece, which is no longer a drawable) — same loud gate a
            # hand-authored manifest would have to pass.
            assemble.validate_animations(bone_anims, bones, paths=paths)
            combined_anims = {
                aname: {**a, "tracks": bone_anims[aname]["tracks"] + piece_tracks.get(aname, [])}
                for aname, a in anims.items()
            }
            pieces.validate_piece_tracks(combined_anims, built)

        images = []
        for p in built:
            images.append((p["name"], p["img"]))
            for v in p["variants"]:
                images.append((v["name"], v["img"]))
        atlas, subtex = pack_atlas.compose_atlas(images, max_width=args.atlas_max_width)

        ske = build_ske_multipiece(args.name, bones, built, bone_anims, piece_tracks,
                                    subtex, fps=args.fps, paths=paths,
                                    ffd_timelines=ffd_timelines or None)
        png_name = f"{args.name}_tex.png"
        tex = build_tex_multi(args.name, atlas.shape[1], atlas.shape[0], png_name, subtex)

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.name}_ske.json").write_text(json.dumps(ske))
        (out / f"{args.name}_tex.json").write_text(json.dumps(tex))
        Image.fromarray(atlas, "RGBA").save(out / png_name)

        debug_viz.render_debug(src_rgba, pieces_list, built, bones,
                                out / f"{args.name}.debug.png")
        debug_viz.save_cutouts(built, out / f"{args.name}.pieces")

        n_variants = sum(len(p["variants"]) for p in built)
        morph_note = f" ({len(morph_pieces)} morph: {', '.join(morph_summary)})" if morph_pieces else ""
        print(f"build_pieces_rig: {len(built)} pieces{morph_note}, {n_variants} variants, "
              f"atlas {atlas.shape[1]}x{atlas.shape[0]}, animations: {', '.join(anims)} "
              f"-> {out}/{args.name}_ske.json (+_tex.json,+_tex.png,+.debug.png)")

    _run_or_fail(body)


if __name__ == "__main__":
    main()
