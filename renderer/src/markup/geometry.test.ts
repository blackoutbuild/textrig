import { describe, expect, it } from 'vitest';
import {
  islandFromStroke, linkGroups, pointInPolygon,
  polygonArea, polygonIsSimple, segIntersect, simplifyRDP, splitPolygon,
  traceSilhouette,
} from './geometry';
import type { Pt } from './geometry';

const square: Pt[] = [[0, 0], [10, 0], [10, 10], [0, 10]];

describe('pointInPolygon', () => {
  it('inside / outside / near-edge', () => {
    expect(pointInPolygon([5, 5], square)).toBe(true);
    expect(pointInPolygon([15, 5], square)).toBe(false);
    expect(pointInPolygon([-0.01, 5], square)).toBe(false);
  });

  it('concave polygon', () => {
    const c: Pt[] = [[0, 0], [10, 0], [10, 10], [6, 10], [6, 4], [4, 4], [4, 10], [0, 10]];
    expect(pointInPolygon([5, 8], c)).toBe(false); // inside the notch
    expect(pointInPolygon([2, 8], c)).toBe(true);
  });
});

describe('polygonArea', () => {
  it('square is 100 regardless of winding', () => {
    expect(polygonArea(square)).toBe(100);
    expect(polygonArea([...square].reverse())).toBe(100);
  });
});

describe('segIntersect', () => {
  it('proper crossing yields the intersection point', () => {
    const hit = segIntersect([0, 0], [10, 10], [0, 10], [10, 0]);
    expect(hit).not.toBeNull();
    expect(hit!.p).toEqual([5, 5]);
    expect(hit!.ta).toBeCloseTo(0.5, 9);
    expect(hit!.tb).toBeCloseTo(0.5, 9);
  });

  it('endpoint touch yields null', () => {
    // segments sharing an endpoint (10,0) — not a proper interior crossing
    expect(segIntersect([0, 0], [10, 0], [10, 0], [10, 10])).toBeNull();
    // b's endpoint lying on a's interior — still not a proper crossing
    expect(segIntersect([0, 0], [10, 0], [5, 0], [5, 10])).toBeNull();
  });
});

describe('polygonIsSimple', () => {
  it('square is simple, bowtie is not', () => {
    expect(polygonIsSimple(square)).toBe(true);
    const bowtie: Pt[] = [[0, 0], [10, 10], [10, 0], [0, 10]];
    expect(polygonIsSimple(bowtie)).toBe(false);
  });
});

describe('splitPolygon', () => {
  it('straight vertical cut splits square into two rects', () => {
    const r = splitPolygon(square, [[4, -5], [4, 15]]);
    if ('error' in r) throw new Error(r.error);
    const areas = r.pieces.map(polygonArea).sort((a, b) => a - b);
    expect(areas[0] + areas[1]).toBe(100);
    expect(areas[0]).toBe(40);
    for (const p of r.pieces) expect(p.length).toBeGreaterThanOrEqual(3);
  });

  it('polyline cut keeps intermediate stroke points', () => {
    const r = splitPolygon(square, [[-5, 5], [5, 6], [15, 5]]);
    if ('error' in r) throw new Error(r.error);
    // the mid point [5,6] must survive in both halves
    expect(r.pieces[0].some(([x, y]) => x === 5 && y === 6)).toBe(true);
    expect(r.pieces[1].some(([x, y]) => x === 5 && y === 6)).toBe(true);
  });

  it('rejects a stroke with fewer than 2 points', () => {
    const r = splitPolygon(square, [[-5, 5]]);
    expect('error' in r && r.error).toMatch(/at least 2 points/);
  });

  it('rejects a cut whose sliver rounds away (degenerate piece)', () => {
    // horizontal cut 0.2 px below the top edge: the sliver's rounded corners
    // collapse onto (0,0)/(10,0) — fewer than 3 corners survive
    const r = splitPolygon(square, [[-5, 0.2], [15, 0.2]]);
    expect('error' in r && r.error).toMatch(/degenerate/);
  });

  it('rejects an endpoint inside the polygon', () => {
    const r = splitPolygon(square, [[5, 5], [15, 5]]);
    expect('error' in r && r.error).toMatch(/OUTSIDE/);
  });

  it('rejects a stroke that misses the polygon', () => {
    const r = splitPolygon(square, [[-5, 20], [15, 20]]);
    expect('error' in r && r.error).toMatch(/exactly twice/);
  });

  it('rejects a stroke crossing four times', () => {
    // zig-zag entering and leaving twice through the top edge
    const r = splitPolygon(square, [[2, -2], [3, 2], [5, -2], [7, 2], [8, -2]]);
    expect('error' in r && r.error).toMatch(/exactly twice/);
  });

  it('cut across a corner region yields a triangle-ish piece', () => {
    const r = splitPolygon(square, [[-2, 3], [3, -2]]);
    if ('error' in r) throw new Error(r.error);
    const areas = r.pieces.map(polygonArea).sort((a, b) => a - b);
    // crossings (0,1) and (1,0) around corner (0,0) -> triangle, area 0.5
    expect(areas[0]).toBeCloseTo(0.5, 5);
  });
});

describe('islandFromStroke', () => {
  it('valid loop normalizes to an integer polygon', () => {
    const r = islandFromStroke([[10.2, 10.4], [30.1, 10], [30, 30], [10, 29.8], [10.2, 10.4]]);
    if ('error' in r) throw new Error(r.error);
    expect(r.polygon).toEqual([[10, 10], [30, 10], [30, 30], [10, 30]]);
  });

  it('rejects too few distinct points and self-intersections', () => {
    expect('error' in islandFromStroke([[5, 5], [5.2, 5.1]])).toBe(true);
    const bowtie: Pt[] = [[0, 0], [10, 10], [10, 0], [0, 10]];
    expect('error' in islandFromStroke(bowtie)).toBe(true);
  });
});

describe('simplifyRDP', () => {
  it('drops collinear midpoints, keeps corners', () => {
    const line: Pt[] = [[0, 0], [5, 0.4], [10, 0], [10, 5], [10, 10]];
    expect(simplifyRDP(line, 1)).toEqual([[0, 0], [10, 0], [10, 10]]);
  });
});

describe('traceSilhouette', () => {
  const grid = (rows: string[]) =>
    (x: number, y: number) => rows[y]?.[x] === '#';

  it('square blob simplifies to 4 corners with the right area', () => {
    const rows = ['........', '.####...', '.####...', '.####...', '........'];
    const poly = traceSilhouette(grid(rows), 8, 5, 1);
    expect(poly.length).toBe(4);
    // pixel-CENTER contour of a 4x3 blob is a 3x2 rectangle
    expect(polygonArea(poly)).toBe(6);
  });

  it('L-shaped blob keeps the concave corner', () => {
    const rows = ['.......', '.##....', '.##....', '.#####.', '.#####.', '.......'];
    const poly = traceSilhouette(grid(rows), 7, 6, 0.5);
    expect(poly.length).toBe(6);
  });

  it('two blobs: the larger one wins', () => {
    const rows = ['##.....', '##.....', '.......', '...####', '...####', '...####'];
    const poly = traceSilhouette(grid(rows), 7, 6, 1);
    // winner is the 4x3 blob on the right
    expect(Math.min(...poly.map((p) => p[0]))).toBeGreaterThanOrEqual(3);
  });

  it('empty grid yields empty polygon', () => {
    expect(traceSilhouette(() => false, 4, 4)).toEqual([]);
  });
});

describe('linkGroups', () => {
  it('links coincident vertices across pieces, not within one', () => {
    const a: Pt[] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    const b: Pt[] = [[10, 0], [20, 0], [20, 10], [10, 10]]; // shares right edge of a
    const groups = linkGroups([a, b]);
    expect(groups.length).toBe(2); // [10,0] pair and [10,10] pair
    for (const g of groups) {
      expect(g.length).toBe(2);
      expect(new Set(g.map((r) => r.piece)).size).toBe(2);
    }
  });

  it('eps tolerance links near-coincident vertices', () => {
    const a: Pt[] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    const b: Pt[] = [[11, 0], [20, 0], [20, 10], [11, 10]];
    expect(linkGroups([a, b], 0.5).length).toBe(0);
    expect(linkGroups([a, b], 1.5).length).toBe(2);
  });
});

