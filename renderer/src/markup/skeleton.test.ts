import { describe, expect, it } from 'vitest';
import { boneTip, parentFirst, tipToRotLen } from './skeleton';
import type { Pt } from './geometry';

describe('boneTip / tipToRotLen', () => {
  it('0° points right, -90° points up (y down)', () => {
    const [rx, ry] = boneTip([10, 20], 0, 5);
    expect(rx).toBeCloseTo(15, 9);
    expect(ry).toBeCloseTo(20, 9);
    const [ux, uy] = boneTip([10, 20], -90, 5);
    expect(ux).toBeCloseTo(10, 9);
    expect(uy).toBeCloseTo(15, 9);
  });

  it('tipToRotLen inverts boneTip', () => {
    const origin: Pt = [100, 50];
    for (const [rot, len] of [[30, 12], [-135, 40], [179, 3]]) {
      const { rotation, length } = tipToRotLen(origin, boneTip(origin, rot, len));
      expect(rotation).toBeCloseTo(rot, 6);
      expect(length).toBeCloseTo(len, 6);
    }
  });

  it('straight-down drag yields rotation 90', () => {
    const { rotation, length } = tipToRotLen([5, 5], [5, 15]);
    expect(rotation).toBeCloseTo(90, 9);
    expect(length).toBeCloseTo(10, 9);
  });
});

describe('parentFirst', () => {
  const b = (name: string, parent: string | null) => ({ name, parent });

  it('already parent-first input keeps its exact order (stable)', () => {
    const list = [b('root', null), b('torso', 'root'), b('head', 'torso'), b('arm', 'torso')];
    expect(parentFirst(list)).toEqual(list);
  });

  it('shuffled input restored: a child never precedes its parent', () => {
    const list = [b('head', 'torso'), b('arm', 'torso'), b('torso', 'root'), b('root', null)];
    const out = parentFirst(list);
    expect([...out.map((x) => x.name)].sort()).toEqual(['arm', 'head', 'root', 'torso']);
    const idx = new Map(out.map((x, i) => [x.name, i]));
    for (const x of out)
      if (x.parent !== null) expect(idx.get(x.parent)!).toBeLessThan(idx.get(x.name)!);
  });

  it('orphan parent counts as a root; a cycle is appended as-is, no hang', () => {
    // orphan: parent name is not in the list — emitted in the first wave
    expect(parentFirst([b('leaf', 'ghost'), b('root', null)]).map((x) => x.name))
      .toEqual(['leaf', 'root']);
    // cycle (unreachable from the UI): appended as-is — the server rejects it; no infinite loop here
    expect(parentFirst([b('a', 'b'), b('b', 'a'), b('root', null)]).map((x) => x.name))
      .toEqual(['root', 'a', 'b']);
  });
});
