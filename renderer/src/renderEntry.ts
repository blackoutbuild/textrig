// Headless render page: plays a DragonBones _ske.json triple with
// pixi-dragonbones-runtime under Pixi 8, deterministically, and returns a filmstrip.
//
// Time driving: PixiFactory.useSharedTicker=false stops the runtime from
// auto-ticking off Pixi's shared ticker; PixiFactory.advanceTime(dtSeconds)
// then steps the DragonBones WorldClock (which buildArmatureDisplay registers
// the armature to) by hand, so each captured frame is at an exact time.
import { GIFEncoder, applyPalette, quantize } from 'gifenc';
import { Application, Assets, Container, Texture } from 'pixi.js';
import { PixiFactory } from 'pixi-dragonbones-runtime';

declare global {
  interface Window {
    __DONE__?: boolean;
    __ERROR__?: string;
    __RESULT__?: {
      filmstrip: string;
      gif?: string;
      checksums: number[];
      armature: string;
      anim: string;
      durationS: number;
      meshCount: number;
      meshVertexChecksums: number[];
    };
  }
}

function toB64(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

// ?asset=/assets/<prefix> (or /out/<prefix>) picks the _ske/_tex triple
// (default: the figure2 test fixture); ?anim=<name> overrides the default first animation;
// ?span=<seconds> caps the capture window (default: min(2s, full duration)).
const q = new URLSearchParams(location.search);
const ASSET = q.get('asset') ?? '/assets/figure2/figure2';
const ANIM_OVERRIDE = q.get('anim');
const SPAN_OVERRIDE = Number(q.get('span'));
// ?zoom=3&fy=0.1 — magnify the auto-fit and aim the viewport at a vertical
// fraction of the armature bounds (0 = top). Used to inspect FFD on the face.
const ZOOM = Number(q.get('zoom')) > 0 ? Number(q.get('zoom')) : 1;
const FOCUS_X = q.has('fx') ? Number(q.get('fx')) : 0.5;
const FOCUS_Y = q.has('fy') ? Number(q.get('fy')) : 0.5;
const SIZE = Number(q.get('size')) > 0 ? Number(q.get('size')) : 300;
const FRAMES = Number(q.get('frames')) > 0 ? Number(q.get('frames')) : 6;
// Sampling mode. 'endpoint' (default): FRAMES samples
// evenly across [0, span] INCLUDING the endpoint (step = span/(FRAMES-1)).
// 'fraction': t_i = (i/FRAMES)*span, i=0..FRAMES-1 — an even loop sampling
// render (renderAt((i/frames)*duration)), so every frame is an even slice of the loop. In
// fraction mode span defaults to the FULL duration (not min(2s,dur)).
const SAMPLE = q.get('sample') === 'fraction' ? 'fraction' : 'endpoint';
// ?gif=1 also encodes a full-loop GIF (gifenc): duration·fps frames at t_i=(i/N)·duration, NETSCAPE loop.
const GIF = q.get('gif') === '1';
const GIF_FPS = 30;

// Sum the live vertex positions of every Mesh-like child (objects whose
// geometry has a position buffer). Rigid motion only changes container
// transforms — this buffer moves ONLY when skinning/FFD writes deformed
// vertices, so distinct per-frame values prove true mesh deformation.
function meshVertexChecksum(root: Container): { count: number; sum: number } {
  let count = 0;
  let sum = 0;
  const visit = (node: Container) => {
    const geom = (node as any).geometry;
    const pos: Float32Array | undefined = geom?.positions ?? geom?.getBuffer?.('aPosition')?.data;
    if (pos && pos.length > 0) {
      count++;
      for (let i = 0; i < pos.length; i++) sum += pos[i];
    }
    for (const c of node.children) visit(c as Container);
  };
  visit(root);
  return { count, sum: Math.round(sum * 1000) / 1000 };
}

async function main() {
  // ---- assets ----
  const [skeRaw, texRaw, texture] = await Promise.all([
    fetch(`${ASSET}_ske.json`).then((r) => {
      if (!r.ok) throw new Error(`ske fetch ${r.status}`);
      return r.json();
    }),
    fetch(`${ASSET}_tex.json`).then((r) => {
      if (!r.ok) throw new Error(`tex.json fetch ${r.status}`);
      return r.json();
    }),
    Assets.load<Texture>(`${ASSET}_tex.png`),
  ]);

  // ---- Pixi 8 app ----
  const app = new Application();
  await app.init({
    width: SIZE,
    height: SIZE,
    backgroundColor: 0x1a1a2e,
    preference: 'webgl',
    preserveDrawingBuffer: true,
  });
  document.body.appendChild(app.canvas);
  // We drive time ourselves; stop Pixi's ticker so nothing double-advances.
  app.ticker.stop();

  // ---- DragonBones factory (deterministic clock) ----
  PixiFactory.useSharedTicker = false; // must be set before first .factory access
  const factory = PixiFactory.factory;

  const dbData = factory.parseDragonBonesData(skeRaw);
  factory.parseTextureAtlasData(texRaw, texture);
  if (!dbData || dbData.armatureNames.length === 0) throw new Error('no armature parsed from ske.json');

  const armatureName = dbData.armatureNames[0];
  const display = factory.buildArmatureDisplay(armatureName);
  if (!display) throw new Error(`buildArmatureDisplay returned null for "${armatureName}"`);

  const anims = display.animation.animationNames;
  if (anims.length === 0) throw new Error('armature has no animations');
  const animName = ANIM_OVERRIDE ?? anims[0];
  const animData = display.animation.animations[animName];
  if (!animData) throw new Error(`animation "${animName}" not found (have: ${anims.join(', ')})`);
  const durationS = animData.duration; // seconds

  app.stage.addChild(display);
  display.animation.play(animName, 0); // 0 = loop forever

  // Prime one tiny step so the first pose is resolved before frame 0, then
  // auto-fit: scale + center the armature's local bounds into the canvas.
  PixiFactory.advanceTime(0.001);
  const b = display.getLocalBounds();
  const fit = ((SIZE * 0.9) / Math.max(b.width, b.height)) * ZOOM;
  display.scale.set(fit);
  display.x = SIZE / 2 - (b.x + b.width * FOCUS_X) * fit;
  display.y = SIZE / 2 - (b.y + b.height * FOCUS_Y) * fit;

  // ---- capture filmstrip ----
  const strip = document.createElement('canvas');
  strip.width = FRAMES * SIZE;
  strip.height = SIZE;
  const sctx = strip.getContext('2d')!;
  const checksums: number[] = [];
  const meshVertexChecksums: number[] = [];
  let meshCount = 0;

  const defaultSpan = SAMPLE === 'fraction' ? durationS : Math.min(2.0, durationS);
  const span = Number.isFinite(SPAN_OVERRIDE) && SPAN_OVERRIDE > 0 ? SPAN_OVERRIDE : defaultSpan;
  // fraction: t_i = (i/FRAMES)*span (endpoint-exclusive, one even slice of the loop per frame).
  // endpoint: t_i = (i/(FRAMES-1))*span (endpoint-inclusive).
  const step = span / (SAMPLE === 'fraction' ? FRAMES : FRAMES - 1);
  let clock = 0.001;
  for (let i = 0; i < FRAMES; i++) {
    const target = i * step;
    PixiFactory.advanceTime(Math.max(0, target - clock));
    clock = target;
    app.renderer.render(app.stage);
    sctx.drawImage(app.canvas, i * SIZE, 0, SIZE, SIZE);
    const d = sctx.getImageData(i * SIZE, 0, SIZE, SIZE).data;
    let sum = 0;
    for (let j = 0; j < d.length; j += 97) sum = (sum + d[j]) % 1_000_000_007;
    checksums.push(sum);
    const mv = meshVertexChecksum(display);
    meshCount = mv.count;
    meshVertexChecksums.push(mv.sum);
  }

  // ---- optional full-loop GIF ----
  let gif: string | undefined;
  if (GIF) {
    const gifFrames = Math.max(2, Math.round(durationS * GIF_FPS));
    // Restart the animation so the GIF opens on the rest pose (advanceTime only
    // steps forward; play(...,0) rewinds the playhead to t=0).
    display.animation.play(animName, 0);
    PixiFactory.advanceTime(0.001);
    const enc = GIFEncoder();
    const tmp = document.createElement('canvas');
    tmp.width = SIZE;
    tmp.height = SIZE;
    const tctx = tmp.getContext('2d')!;
    let gclock = 0.001;
    for (let i = 0; i < gifFrames; i++) {
      const target = (i / gifFrames) * durationS;
      PixiFactory.advanceTime(Math.max(0, target - gclock));
      gclock = target;
      app.renderer.render(app.stage);
      tctx.clearRect(0, 0, SIZE, SIZE);
      tctx.drawImage(app.canvas, 0, 0);
      const { data } = tctx.getImageData(0, 0, SIZE, SIZE);
      const palette = quantize(data, 256);
      // repeat:0 on the first frame writes the NETSCAPE loop extension.
      enc.writeFrame(applyPalette(data, palette), SIZE, SIZE, { palette, delay: Math.round(1000 / GIF_FPS), repeat: 0 });
    }
    enc.finish();
    gif = toB64(enc.bytes());
  }

  window.__RESULT__ = {
    filmstrip: strip.toDataURL('image/png'),
    gif,
    checksums,
    armature: armatureName,
    anim: animName,
    durationS,
    meshCount,
    meshVertexChecksums,
  };
  window.__DONE__ = true;
}

main().catch((e) => {
  window.__ERROR__ = String(e?.stack ?? e);
});
