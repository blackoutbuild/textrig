#!/usr/bin/env bash
# usage: pipeline/build_rig.sh <name> <texture.png> <skeleton.json> <animations.json>
#        [--cols N] [--power P] [--weights-algo idw|heat] [--target db|legacy]  (default db) [--pieces pieces.json]
#        [--underlap N]   (pieces path only; px each piece dives under the ones above, 0 = off)
set -euo pipefail
CALLER=$PWD
NAME=$1; PNG=$2; SKEL=$3; ANIM=$4; shift 4
[[ "$PNG" = /* ]]  || PNG="$CALLER/$PNG"
[[ "$SKEL" = /* ]] || SKEL="$CALLER/$SKEL"
[[ "$ANIM" = /* ]] || ANIM="$CALLER/$ANIM"
cd "$(dirname "$0")"
COLS=16; POWER=4.0; TARGET=db; PIECES=""; WALGO=""; UNDERLAP=""
while [[ $# -gt 0 ]]; do case $1 in
  --cols) COLS=$2; shift 2;; --power) POWER=$2; shift 2;;
  --weights-algo) WALGO=$2; shift 2;;
  --target) TARGET=$2; shift 2;;
  --pieces) PIECES=$2; [[ "$PIECES" = /* ]] || PIECES="$CALLER/$PIECES"; shift 2;;
  --underlap) UNDERLAP=$2; shift 2;;
  *) echo "unknown arg $1"; exit 1;;
esac; done
PY=.venv/bin/python
if [[ -n "$PIECES" ]]; then
  [[ "$TARGET" = db ]] || { echo "--pieces requires --target db"; exit 1; }
  # other knobs use orchestrator defaults; add pass-through when needed
  EXTRA=()
  if [[ -n "$WALGO" ]]; then EXTRA+=(--weights-algo "$WALGO"); fi
  if [[ -n "$UNDERLAP" ]]; then EXTRA+=(--underlap "$UNDERLAP"); fi
  exec $PY build_pieces_rig.py --image "$PNG" --skeleton "$SKEL" --pieces "$PIECES" \
    --animations "$ANIM" --out ../out --name "$NAME" ${EXTRA[@]+"${EXTRA[@]}"}
fi
$PY mesh_gen.py "$PNG" "../out/$NAME.mesh.json" --cols "$COLS"
WARGS=(--power "$POWER" --image "$PNG")
if [[ -n "$WALGO" ]]; then WARGS+=(--algo "$WALGO"); fi
$PY weights_gen.py "../out/$NAME.mesh.json" "$SKEL" "../out/$NAME.weights.json" "${WARGS[@]}"
case $TARGET in
  legacy)
    $PY assemble.py --mesh "../out/$NAME.mesh.json" --skeleton "$SKEL" --weights "../out/$NAME.weights.json" \
      --animations "$ANIM" --texture "$PNG" --out "../out/$NAME.rig.json";;
  db)
    $PY dragonbones_writer.py --mesh "../out/$NAME.mesh.json" --skeleton "$SKEL" --weights "../out/$NAME.weights.json" \
      --animations "$ANIM" --texture "$PNG" --out "../out" --name "$NAME";;
  *) echo "unknown --target $TARGET (want: legacy|db)"; exit 1;;
esac
