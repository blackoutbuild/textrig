// Pure 2D helpers for the markup editor. Image-pixel coordinates, y down.
// No DOM — unit-tested by geometry.test.ts.
export type Pt = [number, number];
export type VRef = { piece: number; vertex: number };

const EPS = 1e-9;

export function pointInPolygon(p: Pt, poly: Pt[]): boolean {
  const [x, y] = p;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function polygonArea(poly: Pt[]): number {
  let s = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++)
    s += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
  return Math.abs(s) / 2;
}

// Proper interior crossing of segments a1-a2 and b1-b2 (touching an endpoint
// does not count) — splitPolygon wants clean crossings only; anything
// degenerate surfaces as a loud "crosses N times" error to the user instead.
export function segIntersect(
  a1: Pt, a2: Pt, b1: Pt, b2: Pt,
): { p: Pt; ta: number; tb: number } | null {
  const dax = a2[0] - a1[0], day = a2[1] - a1[1];
  const dbx = b2[0] - b1[0], dby = b2[1] - b1[1];
  const den = dax * dby - day * dbx;
  if (Math.abs(den) < EPS) return null;
  const ta = ((b1[0] - a1[0]) * dby - (b1[1] - a1[1]) * dbx) / den;
  const tb = ((b1[0] - a1[0]) * day - (b1[1] - a1[1]) * dax) / den;
  if (ta <= EPS || ta >= 1 - EPS || tb <= EPS || tb >= 1 - EPS) return null;
  return { p: [a1[0] + ta * dax, a1[1] + ta * day], ta, tb };
}

export function polygonIsSimple(poly: Pt[]): boolean {
  const n = poly.length;
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue; // adjacent edges
      if (segIntersect(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])) return false;
    }
  return true;
}

export type SplitResult = { pieces: [Pt[], Pt[]] } | { error: string };

// Constraint: pieces.json polygons are integer image pixels — outputs snap
// to that grid, including inherited boundary vertices (assumed already integer).
export function cleanPolygon(p: Pt[]): Pt[] | null {
  const r: Pt[] = [];
  for (const [x, y] of p) {
    const q: Pt = [Math.round(x), Math.round(y)];
    const prev = r[r.length - 1];
    if (prev && prev[0] === q[0] && prev[1] === q[1]) continue;
    r.push(q);
  }
  while (r.length > 1 && r[0][0] === r[r.length - 1][0] && r[0][1] === r[r.length - 1][1]) r.pop();
  return r.length >= 3 ? r : null;
}

// Split a simple polygon in two with a polyline stroke. Both stroke endpoints
// must be OUTSIDE the polygon and the stroke must cross the boundary exactly
// twice; the stroke portion between the crossings becomes the shared new edge.
export function splitPolygon(poly: Pt[], stroke: Pt[]): SplitResult {
  if (stroke.length < 2) return { error: 'cut stroke needs at least 2 points' };
  if (pointInPolygon(stroke[0], poly) || pointInPolygon(stroke[stroke.length - 1], poly))
    return { error: 'both stroke endpoints must be OUTSIDE the piece' };

  type X = { p: Pt; seg: number; ta: number; edge: number; tb: number };
  const n = poly.length;
  const xs: X[] = [];
  for (let s = 0; s < stroke.length - 1; s++)
    for (let e = 0; e < n; e++) {
      const hit = segIntersect(stroke[s], stroke[s + 1], poly[e], poly[(e + 1) % n]);
      if (hit) xs.push({ p: hit.p, seg: s, ta: hit.ta, edge: e, tb: hit.tb });
    }
  xs.sort((a, b) => a.seg - b.seg || a.ta - b.ta);
  if (xs.length !== 2)
    return { error: `stroke must cross the outline exactly twice (crossed ${xs.length} times)` };
  const [x1, x2] = xs;

  // Stroke portion between the two crossings, crossing points included.
  const cut: Pt[] = [x1.p];
  for (let s = x1.seg + 1; s <= x2.seg; s++) cut.push(stroke[s]);
  cut.push(x2.p);

  // Boundary vertices walking FORWARD (polygon order) from crossing a to b:
  // poly[a.edge+1] .. poly[b.edge] inclusive (b's edge starts at poly[b.edge]).
  const chain = (a: X, b: X): Pt[] => {
    if (a.edge === b.edge && b.tb > a.tb) return [];
    const out: Pt[] = [];
    let v = (a.edge + 1) % n;
    for (;;) {
      out.push(poly[v]);
      if (v === b.edge) break;
      v = (v + 1) % n;
    }
    return out;
  };

  const polyA: Pt[] = [...cut, ...chain(x2, x1)];
  const polyB: Pt[] = [...chain(x1, x2), ...[...cut].reverse()];

  const a = cleanPolygon(polyA);
  const b = cleanPolygon(polyB);
  if (!a || !b) return { error: 'cut produces a degenerate piece (fewer than 3 corners)' };
  if (!polygonIsSimple(a) || !polygonIsSimple(b))
    return { error: 'cut produces a self-intersecting outline — simplify the stroke' };
  return { pieces: [a, b] };
}

// Island cut: a stroke that starts AND ends inside a piece closes into a loop
// and becomes a new piece drawn on top — the original polygon stays untouched
// (the covered zone is occluded and diffusion-filled at build, the robot-eye
// pattern). This only validates/normalizes the loop; the editor owns piece
// creation.
export function islandFromStroke(stroke: Pt[]): { polygon: Pt[] } | { error: string } {
  const poly = cleanPolygon(stroke);
  if (!poly) return { error: 'closed stroke is too small — needs ≥3 distinct points' };
  if (!polygonIsSimple(poly))
    return { error: 'closed stroke self-intersects — simplify the loop' };
  if (polygonArea(poly) < 4)
    return { error: 'loop has almost no area — circle a wider region' };
  return { polygon: poly };
}

// Ramer–Douglas–Peucker over an open polyline (first/last points fixed).
export function simplifyRDP(points: Pt[], eps: number): Pt[] {
  if (points.length <= 2) return points.slice();
  const [ax, ay] = points[0];
  const [bx, by] = points[points.length - 1];
  const dx = bx - ax, dy = by - ay;
  const len = Math.hypot(dx, dy);
  let worst = 0, wi = 0;
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i];
    const d = len === 0
      ? Math.hypot(px - ax, py - ay)
      : Math.abs(dy * px - dx * py + bx * ay - by * ax) / len;
    if (d > worst) { worst = d; wi = i; }
  }
  if (worst <= eps) return [points[0], points[points.length - 1]];
  const left = simplifyRDP(points.slice(0, wi + 1), eps);
  const right = simplifyRDP(points.slice(wi), eps);
  return [...left.slice(0, -1), ...right];
}

// Largest outer contour of an opaque region, Moore-neighbor traced and
// RDP-simplified. isOpaque is a callback so tests can feed synthetic grids
// and the editor can feed its ImageData alpha (solid threshold, ≥128).
export function traceSilhouette(
  isOpaque: (x: number, y: number) => boolean, w: number, h: number, eps = 2,
  maxVerts = 120,
): Pt[] {
  const solid = (x: number, y: number) =>
    x >= 0 && y >= 0 && x < w && y < h && isOpaque(x, y);
  // clockwise Moore neighborhood, starting west
  const dirs: Pt[] = [[-1, 0], [-1, -1], [0, -1], [1, -1], [1, 0], [1, 1], [0, 1], [-1, 1]];
  const seen = new Set<number>();
  let best: Pt[] = [];
  let bestArea = 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      if (!solid(x, y) || solid(x - 1, y) || seen.has(y * w + x)) continue;
      // Moore tracing from (x,y); backtrack PIXEL starts at the transparent
      // west neighbor, and is always the last transparent pixel checked
      // before the current one was found — the canonical formulation.
      const contour: Pt[] = [[x, y]];
      seen.add(y * w + x);
      let cx = x, cy = y;
      let bx = x - 1, by = y;
      const cap = w * h * 4;
      for (let step = 0; step < cap; step++) {
        const startDir = dirs.findIndex(([dx, dy]) => dx === bx - cx && dy === by - cy);
        let found = -1;
        for (let k = 1; k <= 8; k++) {
          const di = (startDir + k) % 8;
          const nx = cx + dirs[di][0], ny = cy + dirs[di][1];
          if (solid(nx, ny)) { found = di; break; }
          bx = nx; by = ny;
        }
        if (found < 0) break; // isolated pixel
        cx += dirs[found][0]; cy += dirs[found][1];
        if (cx === x && cy === y) break; // closed the loop
        contour.push([cx, cy]);
        seen.add(cy * w + cx);
      }
      if (contour.length < 3) continue;
      const area = polygonArea(contour); // raw contour — pick BEFORE simplifying
      if (area > bestArea) { bestArea = area; best = contour; }
    }
  if (best.length < 3) return [];
  // Adaptive simplify: a markup polygon needs the shape, not the pixel noise —
  // the build's takeover pass absorbs edge pixels anyway. Grow eps until the
  // vertex count is workable (339-vertex silhouettes drown the editor in
  // handles).
  let e = eps;
  for (;;) {
    const poly = cleanPolygon(simplifyRDP([...best, best[0]], e).slice(0, -1)) ?? [];
    if (poly.length >= 3 && poly.length <= maxVerts) return poly;
    if (e > 64) return poly.length >= 3 ? poly : [];
    e *= 1.6;
  }
}

// Groups of coincident-within-eps vertices spanning 2+ DIFFERENT pieces —
// the editor's shared-boundary handles ("one drag edits both polygons").
// Union-find merges transitively: at a T-junction, two same-piece vertices
// each within eps of one shared vertex land in ONE group — desired, truly
// coincident points must move together.
export function linkGroups(polys: Pt[][], eps = 1.5): VRef[][] {
  const refs: { r: VRef; p: Pt }[] = [];
  polys.forEach((poly, pi) =>
    poly.forEach((p, vi) => refs.push({ r: { piece: pi, vertex: vi }, p })));
  const parent = refs.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < refs.length; i++)
    for (let j = i + 1; j < refs.length; j++) {
      if (refs[i].r.piece === refs[j].r.piece) continue;
      const dx = refs[i].p[0] - refs[j].p[0];
      const dy = refs[i].p[1] - refs[j].p[1];
      if (dx * dx + dy * dy <= eps * eps) parent[find(i)] = find(j);
    }
  const groups = new Map<number, VRef[]>();
  refs.forEach((x, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root)!.push(x.r);
  });
  return [...groups.values()].filter((g) => g.length > 1);
}
