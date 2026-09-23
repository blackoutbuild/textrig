// Skeleton-domain helpers for the markup editor: bone tip/rotation math and
// parent-first ordering. Image-pixel coordinates, y down. No DOM —
// unit-tested by skeleton.test.ts.
import type { Pt } from './geometry';

// Bone tip from origin + absolute rotation (degrees, y down) + length.
// Matches the skeleton.json convention: 0° = right, -90° = up.
export function boneTip(origin: Pt, rotationDeg: number, length: number): Pt {
  const rad = (rotationDeg * Math.PI) / 180;
  return [origin[0] + Math.cos(rad) * length, origin[1] + Math.sin(rad) * length];
}

// Inverse of boneTip: a dragged tip point → new rotation (degrees) + length.
// Tip on the origin degenerates to rotation 0 / length 0 — the editor clamps
// length on drop, so a zero-length bone cannot be committed.
export function tipToRotLen(origin: Pt, tip: Pt): { rotation: number; length: number } {
  const dx = tip[0] - origin[0], dy = tip[1] - origin[1];
  return { rotation: (Math.atan2(dy, dx) * 180) / Math.PI, length: Math.hypot(dx, dy) };
}

// Parent-first order for a bone-like forest: every node comes after its
// parent, input order kept within each wave (stable). Contract helper for
// skeleton.json emitters (boneEditor Done). A parent name missing from the
// list (orphan) counts as a root; a parent CYCLE — unreachable from the
// editors' UI — is appended as-is so the server rejects it loudly instead of
// this helper looping forever.
export function parentFirst<T extends { name: string; parent: string | null }>(list: T[]): T[] {
  const names = new Set(list.map((b) => b.name));
  const placed = new Set<string>();
  const out: T[] = [];
  let rest = list;
  while (rest.length) {
    const ready = rest.filter((b) =>
      b.parent === null || !names.has(b.parent) || placed.has(b.parent));
    if (!ready.length) { out.push(...rest); break; }
    for (const b of ready) { out.push(b); placed.add(b.name); }
    const readySet = new Set(ready);
    rest = rest.filter((b) => !readySet.has(b));
  }
  return out;
}
