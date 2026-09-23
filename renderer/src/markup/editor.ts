// Markup review editor: joints + cut boundaries over the source PNG.
// Session in: /out/markup-<name>.session.json (written by open_markup_review).
// Result out: POST /markup-result/<name> (vite mailbox middleware).
// All geometry/skeleton math lives in ./geometry and ./skeleton (unit-tested);
// this file is DOM only.
import {
  islandFromStroke, linkGroups, pointInPolygon, polygonArea,
  splitPolygon, traceSilhouette,
} from './geometry';
import type { Pt, VRef } from './geometry';
import { boneTip, tipToRotLen } from './skeleton';

type Bone = { name: string; parent: string | null; x: number; y: number;
              rotation?: number; length?: number; [k: string]: unknown };
type PathDef = { name: string; points: Pt[]; [k: string]: unknown };
type Piece = { name: string; source?: { polygon?: Pt[]; [k: string]: unknown };
               [k: string]: unknown };
type Skeleton = Bone[] | { bones: Bone[]; paths?: PathDef[] };
type Session = { name: string; token: string; image: string;
                 imageSize: [number, number] | null;
                 skeleton: Skeleton; pieces: { pieces: Piece[] } | null };

type Handle =
  | { kind: 'bone'; bone: Bone }
  | { kind: 'tip'; bone: Bone }
  | { kind: 'anchor'; path: PathDef; idx: number }
  | { kind: 'verts'; refs: VRef[] };

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const canvas = $<HTMLCanvasElement>('canvas');
const ctx = canvas.getContext('2d')!;

const name = new URLSearchParams(location.search).get('name');
const HIT_PX = 16;         // handle hit radius, CSS px
const SNAP_PX = 8;         // vertex link-snap radius, CSS px
const CHAIN_EPS = 1.5;     // parent-tip == child-origin coincidence, image px
// Canvas bitmap is devicePixelRatio-scaled (resize()), so anything drawn in
// raw canvas units shrinks on retina — route every handle size through px().
const px = (n: number) => n * devicePixelRatio;

let orig: Session;                      // as fetched — reset restores this
let session: Session;                   // working copy, mutated in place
let bones: Bone[] = [];
let paths: PathDef[] = [];
let pieces: Piece[] | null = null;
let newPieces: string[] = [];
let newBones: string[] = [];
let handles: Handle[] = [];
let selectedHandle: Handle | null = null;
let selectedPiece = -1;
let mode: 'edit' | 'cut' = 'edit';
let stroke: Pt[] = [];
let submitted = false;

const img = new Image();
let alpha: ImageData | null = null;     // RGBA of the underlay, for joint hints
const view = { scale: 1, ox: 0, oy: 0 };

const toScreen = (p: Pt): Pt => [p[0] * view.scale + view.ox, p[1] * view.scale + view.oy];
const toImage = (sx: number, sy: number): Pt => [(sx - view.ox) / view.scale, (sy - view.oy) / view.scale];

function toast(msg: string) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3200);
}

function fatal(msg: string) {
  document.body.innerHTML =
    `<div style="margin:auto;max-width:34em;color:#cfd3dc;font:14px system-ui">${msg}</div>`;
}

// ------------------------------------------------------------- data binding

function bindRefs() {
  const sk = session.skeleton;
  bones = Array.isArray(sk) ? sk : sk.bones;
  paths = Array.isArray(sk) ? [] : (sk.paths ?? []);
  pieces = session.pieces?.pieces ?? null;
}

const polys = (): Pt[][] => (pieces ?? []).map((p) => p.source?.polygon ?? []);

function rebuildHandles() {
  handles = bones.map((bone) => ({ kind: 'bone', bone } as Handle));
  for (const bone of bones)
    if (bone.length && bone.rotation !== undefined) handles.push({ kind: 'tip', bone });
  for (const path of paths)
    path.points.forEach((_, idx) => handles.push({ kind: 'anchor', path, idx }));
  if (pieces) {
    const groups = linkGroups(polys());
    const grouped = new Set(groups.flat().map((r) => `${r.piece}:${r.vertex}`));
    for (const g of groups) handles.push({ kind: 'verts', refs: g });
    pieces.forEach((p, pi) =>
      (p.source?.polygon ?? []).forEach((_, vi) => {
        if (!grouped.has(`${pi}:${vi}`))
          handles.push({ kind: 'verts', refs: [{ piece: pi, vertex: vi }] });
      }));
  }
  selectedHandle = null;
}

function handlePos(h: Handle): Pt {
  if (h.kind === 'bone') return [h.bone.x, h.bone.y];
  if (h.kind === 'tip') return boneTip([h.bone.x, h.bone.y], h.bone.rotation!, h.bone.length!);
  if (h.kind === 'anchor') return h.path.points[h.idx];
  const { piece, vertex } = h.refs[0];
  return pieces![piece].source!.polygon![vertex];
}

// The chain illusion: a bone whose tip coincides with a child's origin reads
// as one "knee" point — dragging the knee must bend BOTH segments.
function tipCoveredByChild(bone: Bone): boolean {
  if (!bone.length || bone.rotation === undefined) return false;
  const tp = boneTip([bone.x, bone.y], bone.rotation, bone.length);
  return bones.some((b) => b.parent === bone.name
    && Math.hypot(b.x - tp[0], b.y - tp[1]) <= CHAIN_EPS);
}

function setHandlePos(h: Handle, p: Pt, only?: VRef) {
  if (h.kind === 'bone') { h.bone.x = p[0]; h.bone.y = p[1]; return; }
  if (h.kind === 'tip') {
    const { rotation, length } = tipToRotLen([h.bone.x, h.bone.y], p);
    h.bone.rotation = rotation;
    h.bone.length = length;
    return;
  }
  if (h.kind === 'anchor') { h.path.points[h.idx] = [p[0], p[1]]; return; }
  for (const r of (only ? [only] : h.refs))
    pieces![r.piece].source!.polygon![r.vertex] = [p[0], p[1]];
}

// ------------------------------------------------------------------ layout

function fitView() {
  const w = img.naturalWidth, h = img.naturalHeight;
  const s = Math.min(canvas.width / w, canvas.height / h) * 0.92;
  view.scale = s;
  view.ox = (canvas.width - w * s) / 2;
  view.oy = (canvas.height - h * s) / 2;
}

function resize() {
  canvas.width = canvas.clientWidth * devicePixelRatio;
  canvas.height = canvas.clientHeight * devicePixelRatio;
}

// ------------------------------------------------------------------- paint

const pieceColor = (n: string) => {
  let hash = 0;
  for (const ch of n) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return `hsl(${((hash % 360) + 360) % 360} 75% 62%)`;
};

function alphaAt(p: Pt): number {
  if (!alpha) return 255;
  const x = Math.round(p[0]), y = Math.round(p[1]);
  if (x < 0 || y < 0 || x >= alpha.width || y >= alpha.height) return 0;
  return alpha.data[(y * alpha.width + x) * 4 + 3];
}

function draw() {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = '#16181d';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  // checker behind alpha
  ctx.save();
  ctx.translate(view.ox, view.oy);
  ctx.scale(view.scale, view.scale);
  ctx.fillStyle = '#22252c';
  ctx.fillRect(0, 0, img.naturalWidth, img.naturalHeight);
  ctx.imageSmoothingEnabled = view.scale < 3;
  ctx.drawImage(img, 0, 0);
  ctx.restore();

  const showSkel = $<HTMLInputElement>('layer-skel').checked;
  const showCuts = $<HTMLInputElement>('layer-cuts').checked;

  if (showCuts && pieces) {
    pieces.forEach((p, pi) => {
      const poly = p.source?.polygon;
      if (!poly?.length) return;
      ctx.beginPath();
      poly.forEach((pt, i) => {
        const [x, y] = toScreen(pt);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.closePath();
      ctx.strokeStyle = pieceColor(p.name);
      ctx.lineWidth = px(pi === selectedPiece ? 2.5 : 1.25);
      ctx.stroke();
      if (pi === selectedPiece) {
        ctx.fillStyle = pieceColor(p.name).replace(')', ' / 0.12)');
        ctx.fill();
      }
      const [lx, ly] = toScreen(poly[0]);
      ctx.fillStyle = pieceColor(p.name);
      ctx.font = `${px(12)}px system-ui`;
      ctx.fillText(p.name, lx + px(4), ly - px(4));
    });
  }

  if (showSkel) {
    const byName = new Map(bones.map((b) => [b.name, b]));
    ctx.lineWidth = px(1);
    ctx.strokeStyle = '#8b93a3';
    ctx.setLineDash([4, 3]);
    for (const b of bones) {
      const parent = b.parent ? byName.get(b.parent) : null;
      if (!parent) continue;
      const [x1, y1] = toScreen([parent.x, parent.y]);
      const [x2, y2] = toScreen([b.x, b.y]);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    ctx.setLineDash([]);
    for (const b of bones) {
      if (b.length && b.rotation !== undefined) {
        const rad = (b.rotation * Math.PI) / 180;
        const [x1, y1] = toScreen([b.x, b.y]);
        const [x2, y2] = toScreen([b.x + Math.cos(rad) * b.length, b.y + Math.sin(rad) * b.length]);
        ctx.strokeStyle = '#4b5563';
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      }
    }
    for (const path of paths) {
      ctx.strokeStyle = '#c084fc';
      ctx.beginPath();
      path.points.forEach((pt, i) => {
        const [x, y] = toScreen(pt);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
  }

  // handles on top
  for (const h of handles) {
    if (h.kind !== 'verts' && !showSkel) continue;
    if (h.kind === 'verts' && !showCuts) continue;
    const [x, y] = toScreen(handlePos(h));
    const sel = h === selectedHandle;
    if (h.kind === 'bone') {
      ctx.fillStyle = alphaAt([h.bone.x, h.bone.y]) === 0 ? '#ef4444' : '#22d3ee';
      ctx.beginPath(); ctx.arc(x, y, px(sel ? 11 : 8), 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#cfd3dc'; ctx.font = `${px(12)}px system-ui`;
      ctx.fillText(h.bone.name, x + px(12), y - px(10));
    } else if (h.kind === 'tip') {
      if (tipCoveredByChild(h.bone)) continue; // chain knee — child's circle IS the handle
      ctx.strokeStyle = '#22d3ee';
      ctx.lineWidth = px(2.5);
      ctx.beginPath(); ctx.arc(x, y, px(sel ? 10 : 7), 0, Math.PI * 2); ctx.stroke();
    } else if (h.kind === 'anchor') {
      const r = px(sel ? 11 : 8);
      ctx.fillStyle = '#c084fc';
      ctx.beginPath();
      ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y);
      ctx.closePath(); ctx.fill();
    } else {
      const linked = h.refs.length > 1;
      ctx.fillStyle = linked ? '#fbbf24' : '#9ca3af';
      const r = px((sel ? 17 : 13) + (linked ? 3 : 0));
      ctx.fillRect(x - r / 2, y - r / 2, r, r);
    }
  }

  // cut stroke in progress
  if (stroke.length) {
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = px(1.5);
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    stroke.forEach((pt, i) => {
      const [x, y] = toScreen(pt);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }
  requestAnimationFrame(draw);
}

// -------------------------------------------------------------- interaction

let drag:
  | { h: Handle; only?: VRef; chainParent?: Bone; pinTip?: Pt }
  | { pan: true; sx: number; sy: number; ox: number; oy: number }
  | null = null;
let cutting = false;   // brush stroke in progress (cut mode, button held)

const eventPos = (e: MouseEvent): Pt => {
  const r = canvas.getBoundingClientRect();
  return [(e.clientX - r.left) * devicePixelRatio, (e.clientY - r.top) * devicePixelRatio];
};

function hitHandle(sx: number, sy: number): Handle | null {
  const showSkel = $<HTMLInputElement>('layer-skel').checked;
  const showCuts = $<HTMLInputElement>('layer-cuts').checked;
  let best: Handle | null = null;
  let bestD = HIT_PX * devicePixelRatio;
  for (const h of handles) {
    if (h.kind !== 'verts' && !showSkel) continue;
    if (h.kind === 'verts' && !showCuts) continue;
    if (h.kind === 'tip' && tipCoveredByChild(h.bone)) continue; // hidden under the knee
    const [x, y] = toScreen(handlePos(h));
    const d = Math.hypot(x - sx, y - sy);
    if (d < bestD) { bestD = d; best = h; }
  }
  return best;
}

canvas.addEventListener('mousedown', (e) => {
  if (submitted) return;
  const [sx, sy] = eventPos(e);
  if (mode === 'cut') {
    // brush: press starts a fresh stroke (a failed previous one is discarded),
    // drag draws it live, release applies the cut
    cutting = true;
    stroke = [toImage(sx, sy).map(Math.round) as Pt];
    return;
  }
  const h = hitHandle(sx, sy);
  if (h) {
    selectedHandle = h;
    let only: VRef | undefined;
    if (h.kind === 'verts' && e.altKey && h.refs.length > 1) {
      // Alt-drag: detach — move only the member nearest the cursor
      only = h.refs.reduce((a, b) => {
        const pa = toScreen(pieces![a.piece].source!.polygon![a.vertex]);
        const pb = toScreen(pieces![b.piece].source!.polygon![b.vertex]);
        return Math.hypot(pa[0] - sx, pa[1] - sy) <= Math.hypot(pb[0] - sx, pb[1] - sy) ? a : b;
      });
    }
    // Joint on a chain: bend neighbours instead of tearing them off.
    // Alt-drag skips this — plain origin translate (detach escape hatch).
    let chainParent: Bone | undefined;
    let pinTip: Pt | undefined;
    if (h.kind === 'bone' && !e.altKey) {
      const parent = h.bone.parent ? bones.find((b) => b.name === h.bone.parent) : undefined;
      if (parent && tipCoveredByChild(parent)) {
        const t = boneTip([parent.x, parent.y], parent.rotation!, parent.length!);
        if (Math.hypot(t[0] - h.bone.x, t[1] - h.bone.y) <= CHAIN_EPS) chainParent = parent;
      }
      if (tipCoveredByChild(h.bone))
        pinTip = boneTip([h.bone.x, h.bone.y], h.bone.rotation!, h.bone.length!);
    }
    drag = { h, only, chainParent, pinTip };
    return;
  }
  // piece select (topmost = later in list), else pan
  const ip = toImage(sx, sy);
  if (pieces) {
    for (let i = pieces.length - 1; i >= 0; i--) {
      const poly = pieces[i].source?.polygon;
      if (poly && pointInPolygon(ip, poly)) {
        selectedPiece = i;
        syncPanel();
        return;
      }
    }
    selectedPiece = -1;
    syncPanel();
  }
  drag = { pan: true, sx, sy, ox: view.ox, oy: view.oy };
});

canvas.addEventListener('mousemove', (e) => {
  if (cutting && mode === 'cut') {
    const [sx, sy] = eventPos(e);
    const p = toImage(sx, sy);
    const last = stroke[stroke.length - 1];
    const minD = (3 * devicePixelRatio) / view.scale; // decimate to ~3 CSS px
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= minD)
      stroke.push([Math.round(p[0]), Math.round(p[1])]);
    return;
  }
  if (!drag) return;
  const [sx, sy] = eventPos(e);
  if ('pan' in drag) {
    view.ox = drag.ox + (sx - drag.sx);
    view.oy = drag.oy + (sy - drag.sy);
    return;
  }
  setHandlePos(drag.h, toImage(sx, sy), drag.only);
  if (drag.h.kind === 'bone') {
    const b = drag.h.bone;
    if (drag.chainParent) {
      // parent tip follows the knee
      const r = tipToRotLen([drag.chainParent.x, drag.chainParent.y], [b.x, b.y]);
      drag.chainParent.rotation = r.rotation;
      drag.chainParent.length = Math.max(1, r.length);
    }
    if (drag.pinTip) {
      // own tip stays pinned where the next chain joint sits
      const r = tipToRotLen([b.x, b.y], drag.pinTip);
      b.rotation = r.rotation;
      b.length = Math.max(1, r.length);
    }
  }
});

canvas.addEventListener('mouseup', (e) => {
  if (cutting) {
    cutting = false;
    if (stroke.length >= 2) applyCut();
    else stroke = []; // a click without a drag is not a stroke
    return;
  }
  if (!drag) return;
  if (!('pan' in drag)) {
    const h = drag.h;
    if (h.kind === 'tip') {
      // Dropped near a direct child's origin — snap: forms a chain knee.
      const [tx, ty] = toScreen(handlePos(h));
      let snap: Bone | null = null;
      let bestD = SNAP_PX * devicePixelRatio;
      for (const b of bones) {
        if (b.parent !== h.bone.name) continue;
        const [bx, by] = toScreen([b.x, b.y]);
        const d = Math.hypot(bx - tx, by - ty);
        if (d < bestD) { bestD = d; snap = b; }
      }
      if (snap) {
        const r = tipToRotLen([h.bone.x, h.bone.y], [snap.x, snap.y]);
        h.bone.rotation = r.rotation;
        h.bone.length = r.length;
        toast(`tip of "${h.bone.name}" snapped to joint "${snap.name}"`);
      }
      // Rounding the tip POINT would smear rotation/length — round the fields.
      h.bone.rotation = Math.round((h.bone.rotation as number) * 10) / 10;
      h.bone.length = Math.max(1, Math.round(h.bone.length as number));
      drag = null;
      return;
    }
    // On Alt-drag only one group member moved — anchor on IT, not refs[0]
    const p = drag.only
      ? pieces![drag.only.piece].source!.polygon![drag.only.vertex]
      : handlePos(h);
    const rounded: Pt = [Math.round(p[0]), Math.round(p[1])];
    setHandlePos(h, rounded, drag.only);
    if (h.kind === 'bone') {
      // re-derive chained segments from the ROUNDED knee, then round fields
      if (drag.chainParent) {
        const r = tipToRotLen([drag.chainParent.x, drag.chainParent.y], rounded);
        drag.chainParent.rotation = Math.round(r.rotation * 10) / 10;
        drag.chainParent.length = Math.max(1, Math.round(r.length));
      }
      if (drag.pinTip) {
        const r = tipToRotLen(rounded, drag.pinTip);
        h.bone.rotation = Math.round(r.rotation * 10) / 10;
        h.bone.length = Math.max(1, Math.round(r.length));
      }
    }
    // vertex drags snap-link to another piece's vertex within SNAP_PX
    if (h.kind === 'verts' && pieces) {
      const dragged = new Set((drag.only ? [drag.only] : h.refs).map((r) => r.piece));
      const [hx, hy] = toScreen(rounded);
      let snap: Pt | null = null;
      let bestD = SNAP_PX * devicePixelRatio;
      pieces.forEach((pc, pi) => {
        if (dragged.has(pi)) return;
        (pc.source?.polygon ?? []).forEach((v) => {
          const [vx, vy] = toScreen(v);
          const d = Math.hypot(vx - hx, vy - hy);
          if (d < bestD) { bestD = d; snap = v; }
        });
      });
      const snapped: Pt | null = snap;
      if (snapped) setHandlePos(h, [snapped[0], snapped[1]], drag.only);
      rebuildHandles(); // link groups may have changed
      // keep the dropped vertex selected so drag→Delete works
      const final: Pt = snapped ? [snapped[0], snapped[1]] : rounded;
      selectedHandle = handles.find((hh) => {
        if (hh.kind !== 'verts') return false;
        const q = handlePos(hh);
        return q[0] === final[0] && q[1] === final[1];
      }) ?? null;
    }
  }
  drag = null;
});

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const [sx, sy] = eventPos(e);
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  const ns = Math.min(16, Math.max(0.1, view.scale * factor));
  view.ox = sx - ((sx - view.ox) / view.scale) * ns;
  view.oy = sy - ((sy - view.oy) / view.scale) * ns;
  view.scale = ns;
}, { passive: false });

function splitBone(bone: Bone, t: number) {
  const tip = boneTip([bone.x, bone.y], bone.rotation!, bone.length!);
  const cut: Pt = [Math.round(bone.x + (tip[0] - bone.x) * t),
                   Math.round(bone.y + (tip[1] - bone.y) * t)];
  const rest = bone.length! * (1 - t);
  bone.length = Math.max(1, Math.round(bone.length! * t));
  let n = 1;
  while (bones.some((b) => b.name === `${bone.name}_seg${n}`)) n++;
  const child: Bone = { name: `${bone.name}_seg${n}`, parent: bone.name,
                        x: cut[0], y: cut[1], rotation: bone.rotation,
                        length: Math.max(1, Math.round(rest)) };
  bones.splice(bones.indexOf(bone) + 1, 0, child); // parent-first order holds
  newBones.push(child.name);
  rebuildHandles();
  syncPanel();
  toast(`bone "${bone.name}" split: new joint "${child.name}"`);
}

canvas.addEventListener('dblclick', (e) => {
  if (submitted) return;
  const [sx, sy] = eventPos(e);
  if (mode === 'cut') { applyCut(); return; }
  const showSkel = $<HTMLInputElement>('layer-skel').checked;
  const ip = toImage(sx, sy);
  const maxD = (8 * devicePixelRatio) / view.scale;
  // candidates: nearest polygon edge (insert vertex) vs bone axis (insert joint)
  let best: { piece: number; after: number; p: Pt; d: number } | null = null;
  (pieces ?? []).forEach((pc, pi) => {
    const poly = pc.source?.polygon;
    if (!poly) return;
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i], b = poly[(i + 1) % poly.length];
      const abx = b[0] - a[0], aby = b[1] - a[1];
      const len2 = abx * abx + aby * aby || 1;
      const t = Math.max(0.05, Math.min(0.95,
        ((ip[0] - a[0]) * abx + (ip[1] - a[1]) * aby) / len2));
      const q: Pt = [a[0] + t * abx, a[1] + t * aby];
      const d = Math.hypot(q[0] - ip[0], q[1] - ip[1]);
      if (d <= maxD && (!best || d < best.d)) best = { piece: pi, after: i, p: q, d };
    }
  });
  let bestBone: { bone: Bone; t: number; d: number } | null = null;
  if (showSkel)
    for (const b of bones) {
      if (!b.length || b.rotation === undefined) continue;
      const tip = boneTip([b.x, b.y], b.rotation, b.length);
      const abx = tip[0] - b.x, aby = tip[1] - b.y;
      const len2 = abx * abx + aby * aby || 1;
      const t = Math.max(0.1, Math.min(0.9,
        ((ip[0] - b.x) * abx + (ip[1] - b.y) * aby) / len2));
      const q: Pt = [b.x + t * abx, b.y + t * aby];
      const d = Math.hypot(q[0] - ip[0], q[1] - ip[1]);
      if (d <= maxD && (!bestBone || d < bestBone.d)) bestBone = { bone: b, t, d };
    }
  const edge = best as { piece: number; after: number; p: Pt; d: number } | null;
  const axis = bestBone as { bone: Bone; t: number; d: number } | null;
  if (axis && (!edge || axis.d <= edge.d)) return splitBone(axis.bone, axis.t);
  if (edge) {
    const poly = pieces![edge.piece].source!.polygon!;
    poly.splice(edge.after + 1, 0, [Math.round(edge.p[0]), Math.round(edge.p[1])]);
    rebuildHandles();
  }
});

window.addEventListener('keydown', (e) => {
  if (submitted || e.target instanceof HTMLTextAreaElement) return;
  if (e.key === 'v' || e.key === 'V') setMode('edit');
  if (e.key === 'c' || e.key === 'C') setMode('cut');
  if (e.key === 'Escape') {
    if (stroke.length) { stroke = []; cutting = false; }
    else { setMode('edit'); selectedPiece = -1; syncPanel(); }
  }
  if (e.key === 'Enter' && mode === 'cut') applyCut();
  if ((e.key === 'Delete' || e.key === 'Backspace') && selectedHandle?.kind === 'verts')
    deleteVertex(selectedHandle);
});

function deleteVertex(h: Handle & { kind: 'verts' }) {
  if (!pieces) return;
  // a same-piece T-junction group can hold 2+ refs of ONE piece — count per piece
  const perPiece = new Map<number, number>();
  for (const r of h.refs) perPiece.set(r.piece, (perPiece.get(r.piece) ?? 0) + 1);
  for (const [pi, count] of perPiece)
    if (pieces[pi].source!.polygon!.length - count < 3)
      return toast(`"${pieces[pi].name}" has only 3 vertices — nothing to delete`);
  // delete highest vertex index first within each piece
  [...h.refs].sort((a, b) => b.vertex - a.vertex)
    .forEach((r) => pieces![r.piece].source!.polygon!.splice(r.vertex, 1));
  rebuildHandles();
}

function deletePiece() {
  if (submitted) return toast('already sent to the agent — edits are locked (the agent opens the next round)');
  if (!pieces) return;
  if (selectedPiece < 0) return toast('select a piece first by clicking inside it');
  if (pieces.length === 1) return toast('this is the last piece — nothing to delete');
  const [gone] = pieces.splice(selectedPiece, 1);
  newPieces = newPieces.filter((n) => n !== gone.name);
  selectedPiece = -1;
  setMode('edit');
  rebuildHandles();
  syncPanel();
  toast(`piece "${gone.name}" deleted (its region leaves the rig entirely)`);
}

// ---------------------------------------------------------------- cut tool

function setMode(m: 'edit' | 'cut') {
  if (m === 'cut' && selectedPiece < 0)
    return toast('select a piece first by clicking inside it');
  mode = m;
  stroke = [];
  cutting = false;
  $('tool-edit').classList.toggle('active', m === 'edit');
  $('tool-cut').classList.toggle('active', m === 'cut');
}

function cutChild(piece: Piece, polygon: Pt[]): Piece {
  let n = 1;
  while (pieces!.some((p) => p.name === `${piece.name}_cut${n}`)) n++;
  const clone: Piece = JSON.parse(JSON.stringify(piece));
  delete (clone as Record<string, unknown>).variants; // variant offsets belong to the original region
  clone.name = `${piece.name}_cut${n}`;
  clone.source = { ...clone.source, polygon };
  pieces!.splice(pieces!.indexOf(piece) + 1, 0, clone);
  newPieces.push(clone.name);
  return clone;
}

function applyCut() {
  if (!pieces || selectedPiece < 0) return toast('no piece selected');
  const piece = pieces[selectedPiece];
  const poly = piece.source?.polygon;
  if (!poly) return toast('this piece has no polygon (file layer) — nothing to cut');
  const inFirst = stroke.length > 0 && pointInPolygon(stroke[0], poly);
  const inLast = stroke.length > 0 && pointInPolygon(stroke[stroke.length - 1], poly);
  if (inFirst !== inLast)
    return toast('two stroke shapes: through-cut (both ends OUTSIDE the piece) '
      + 'or island (both ends INSIDE; the loop closes itself)');
  let clone: Piece;
  if (inFirst && inLast) {
    // island: the loop becomes a piece ON TOP, the original polygon stays —
    // the covered zone is occluded and diffusion-filled at build (eye pattern)
    const r = islandFromStroke(stroke);
    if ('error' in r) return toast(r.error);
    clone = cutChild(piece, r.polygon);
  } else {
    const r = splitPolygon(poly, stroke);
    if ('error' in r) return toast(r.error);
    const [a, b] = r.pieces;
    const [keep, cutOff] = polygonArea(a) >= polygonArea(b) ? [a, b] : [b, a];
    clone = cutChild(piece, cutOff);
    piece.source!.polygon = keep;
  }
  stroke = [];
  // stay in cut mode — brush cutting is serial (V/Escape exits)
  rebuildHandles();
  syncPanel();
  toast(`cut: new piece "${clone.name}" (the agent assigns its bone)`);
}

// Stroke cuts need a region to cut FROM, so an empty session auto-traces the
// whole character into one starting piece at boot — no button to remember.
// With `wipe` the agent's piece markup is dropped first (the "reset EVERYTHING"
// case): back to the clean silhouette; "reset edits" still
// returns to the agent's original.
function traceSilhouettePiece(wipe = false) {
  if (submitted) return toast('already sent to the agent — edits are locked (the agent opens the next round)');
  if (!alpha) return toast('image alpha channel unavailable — nothing to trace');
  const a = alpha;
  // threshold 200: baked glow/aura often passes 128 and the contour chases it
  const poly = traceSilhouette(
    (x, y) => a.data[(y * a.width + x) * 4 + 3] >= 200, a.width, a.height, 2);
  if (poly.length < 3) return toast('no solid silhouette found (alpha ≥128 is empty)');
  if (wipe || !session.pieces) {
    session.pieces = { pieces: [] };
    newPieces = [];
  }
  ($('tool-cut') as HTMLButtonElement).disabled = false;
  ($('del-piece') as HTMLButtonElement).disabled = false;
  $<HTMLInputElement>('layer-cuts').disabled = false;
  bindRefs();
  let n = 0;
  let name = 'silhouette';
  while (pieces!.some((p) => p.name === name)) name = `silhouette${++n}`;
  const piece: Piece = { name, bone: bones[0]?.name ?? 'root', type: 'rigid',
                         source: { polygon: poly } };
  pieces!.unshift(piece); // bottom of the draw order
  newPieces.push(name);
  selectedPiece = pieces!.indexOf(piece);
  stroke = [];
  setMode('edit');
  rebuildHandles();
  syncPanel();
  toast(wipe
    ? `piece markup reset: clean silhouette "${name}" (${poly.length} vertices) — cut it with strokes`
    : `silhouette traced automatically: "${name}" (${poly.length} vertices) — cut it with strokes`);
}

// ------------------------------------------------------------------- panel

function syncPanel() {
  $('sel-piece').textContent = selectedPiece >= 0 && pieces
    ? `selected: ${pieces[selectedPiece].name}`
    : 'no piece selected';
  const extra = [
    newPieces.length ? `new pieces: ${newPieces.join(', ')}` : '',
    newBones.length ? `new bones: ${newBones.join(', ')}` : '',
  ].filter(Boolean).join('\n');
  $('status').textContent = submitted ? 'sent to the agent ✓' : extra;
}

$('tool-edit').onclick = () => setMode('edit');
$('tool-cut').onclick = () => setMode('cut');
$('del-piece').onclick = deletePiece;
$('trace-sil').onclick = () => traceSilhouettePiece(true);

$('reset').onclick = () => {
  if (submitted) return; // what was sent must stay what was sent
  session = structuredClone(orig);
  newPieces = [];
  newBones = [];
  selectedPiece = -1;
  stroke = [];
  setMode('edit');
  bindRefs();
  rebuildHandles();
  syncPanel();
  if (!pieces?.length) traceSilhouettePiece(); // empty original → same auto-start
};

$('done').onclick = async () => {
  if (submitted) return;
  const body = {
    token: session.token,
    skeleton: session.skeleton,
    pieces: session.pieces,
    meta: { new_pieces: newPieces, new_bones: newBones,
            user_note: $<HTMLTextAreaElement>('note').value.trim() },
  };
  try {
    const resp = await fetch(`/markup-result/${session.name}`, {
      method: 'POST', body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    submitted = true;
    ($('done') as HTMLButtonElement).disabled = true;
    syncPanel();
  } catch (err) {
    toast(`send failed: ${err} — is the preview server running?`);
  }
};

// -------------------------------------------------------------------- boot

async function boot() {
  if (!name) return fatal('no ?name= in the URL — the agent must call open_markup_review');
  let resp: Response;
  try {
    resp = await fetch(`/out/markup-${name}.session.json`, { cache: 'no-store' });
  } catch {
    return fatal('preview server is not responding');
  }
  if (!resp.ok)
    return fatal(`no session "${name}" — the agent must call open_markup_review`);
  orig = await resp.json();
  session = structuredClone(orig);
  // Resume: a result already POSTed for THIS session round (same token)
  // restores the edits — reloads and code updates must not eat human work.
  // "Reset edits" still returns to the untouched original.
  try {
    const rr = await fetch(`/out/markup-${name}.result.json`, { cache: 'no-store' });
    if (rr.ok) {
      const prev = await rr.json();
      if (prev?.token === orig.token && prev.skeleton) {
        session.skeleton = prev.skeleton;
        if (prev.pieces) session.pieces = prev.pieces;
        newPieces = prev.meta?.new_pieces ?? [];
        newBones = prev.meta?.new_bones ?? [];
        $<HTMLTextAreaElement>('note').value = prev.meta?.user_note ?? '';
        toast('restored edits from the last submission of this session');
      }
    }
  } catch { /* no stored result on the preview server — clean start */ }
  bindRefs();
  $('title').textContent = `markup: ${session.name}`;
  if (!pieces) {
    ($('tool-cut') as HTMLButtonElement).disabled = true;
    ($('del-piece') as HTMLButtonElement).disabled = true;
    $<HTMLInputElement>('layer-cuts').disabled = true;
  }

  let loaded = true;
  await new Promise<void>((ok, bad) => {
    img.onload = () => ok();
    img.onerror = () => bad(new Error('image load failed'));
    img.src = session.image;
  }).catch(() => { loaded = false; fatal('session image failed to load'); });
  if (!loaded) return;

  const off = document.createElement('canvas');
  off.width = img.naturalWidth; off.height = img.naturalHeight;
  const octx = off.getContext('2d')!;
  octx.drawImage(img, 0, 0);
  try { alpha = octx.getImageData(0, 0, off.width, off.height); } catch { alpha = null; }

  resize();
  fitView();
  rebuildHandles();
  syncPanel();
  // no piece markup at all → auto-trace the silhouette so strokes have
  // something to cut (the mandatory step must not be a button)
  if (!pieces?.length) traceSilhouettePiece();
  requestAnimationFrame(draw);
}

window.addEventListener('resize', () => { resize(); });
boot();
