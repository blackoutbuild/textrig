// Live preview page (v2 feature 1): plays a generated DragonBones triple
// out/<name>_ske.json + _tex.json + _tex.png with the real product runtime
// (pixi-dragonbones-runtime under Pixi 8) at full fps/palette/alpha — the
// GIF is only a chat artifact, THIS is what quality acceptance looks at.
//
// Time driving mirrors the headless renderer: useSharedTicker=false, and the
// app ticker advances the DragonBones WorldClock by hand — that one knob
// gives pause, single-frame step and speed without touching the runtime.
import { Application, Assets, Texture } from 'pixi.js';
import { PixiFactory } from 'pixi-dragonbones-runtime';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const rigSel = $<HTMLSelectElement>('rig');
const animSel = $<HTMLSelectElement>('anim');
const playPauseBtn = $<HTMLButtonElement>('playPause');
const stepBtn = $<HTMLButtonElement>('step');
const speedInput = $<HTMLInputElement>('speed');
const speedVal = $<HTMLSpanElement>('speedVal');
const bgSel = $<HTMLSelectElement>('bg');
const bgColor = $<HTMLInputElement>('bgColor');
const banner = $<HTMLDivElement>('banner');

const q = new URLSearchParams(location.search);
const STEP_S = 1 / 30;

let currentRig: string | null = null;
let paused = false;
let speed = 1;

const showError = (msg: string) => {
  banner.textContent = msg;
  banner.style.display = 'block';
};

const gotoRig = (name: string) => {
  const url = new URL(location.href);
  url.searchParams.set('name', name);
  url.searchParams.delete('anim');
  location.href = url.toString(); // full navigation = clean armature/state reset
};

async function loadRigList(): Promise<string[]> {
  const rigs: string[] = await fetch('/rigs').then((r) => r.json());
  rigSel.replaceChildren(...rigs.map((n) => new Option(n, n)));
  if (currentRig && rigs.includes(currentRig)) rigSel.value = currentRig;
  return rigs;
}

// ---- controls wired once, independent of rig load success ----
rigSel.onchange = () => gotoRig(rigSel.value);
speedInput.oninput = () => {
  speed = Number(speedInput.value);
  speedVal.textContent = `${speed.toFixed(2)}x`;
};
const setPaused = (p: boolean) => {
  paused = p;
  playPauseBtn.textContent = paused ? '▶' : '⏸';
};
playPauseBtn.onclick = () => setPaused(!paused);
stepBtn.onclick = () => {
  setPaused(true);
  PixiFactory.advanceTime(STEP_S);
};
window.addEventListener('keydown', (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
  if (e.code === 'Space') { e.preventDefault(); playPauseBtn.click(); }
  if (e.key === '.') stepBtn.click();
});
const applyBg = () => {
  document.body.className = `bg-${bgSel.value}`;
  bgColor.style.display = bgSel.value === 'custom' ? '' : 'none';
  document.body.style.setProperty('--custom-bg', bgColor.value);
};
bgSel.onchange = applyBg;
bgColor.oninput = applyBg;

// Vite dev websocket: reload when the shown rig is rebuilt, refresh the
// dropdown when some other rig (re)appears in out/.
if (import.meta.hot) {
  import.meta.hot.on('rig-rebuilt', ({ name }: { name: string }) => {
    if (name === currentRig) location.reload();
    else void loadRigList();
  });
}

async function main() {
  const rigs = await loadRigList();
  if (rigs.length === 0) {
    showError('no rigs in out/ — build one first:\npipeline/build_rig.sh <name> … --target db');
    return;
  }
  currentRig = q.get('name') ?? rigs[0];
  rigSel.value = currentRig;
  if (!rigs.includes(currentRig)) {
    showError(`rig "${currentRig}" not built — run pipeline/build_rig.sh … --target db\navailable: ${rigs.join(', ')}`);
    return;
  }

  const asset = `/out/${currentRig}`;
  const [skeRaw, texRaw, texture] = await Promise.all([
    fetch(`${asset}_ske.json`).then((r) => { if (!r.ok) throw new Error(`ske fetch ${r.status}`); return r.json(); }),
    fetch(`${asset}_tex.json`).then((r) => { if (!r.ok) throw new Error(`tex.json fetch ${r.status}`); return r.json(); }),
    Assets.load<Texture>(`${asset}_tex.png`),
  ]);

  const app = new Application();
  await app.init({ resizeTo: window, backgroundAlpha: 0, preference: 'webgl', antialias: true });
  document.body.appendChild(app.canvas);

  PixiFactory.useSharedTicker = false; // we advance the WorldClock ourselves
  const factory = PixiFactory.factory;
  const dbData = factory.parseDragonBonesData(skeRaw);
  factory.parseTextureAtlasData(texRaw, texture);
  if (!dbData || dbData.armatureNames.length === 0) throw new Error('no armature parsed from ske.json');
  const display = factory.buildArmatureDisplay(dbData.armatureNames[0]);
  if (!display) throw new Error('buildArmatureDisplay returned null');
  app.stage.addChild(display);

  // ---- animations ----
  const anims = display.animation.animationNames;
  if (anims.length === 0) throw new Error('armature has no animations');
  animSel.replaceChildren(...anims.map((n) => new Option(n, n)));
  const playAnim = (name: string) => {
    display.animation.play(name, 0);
    PixiFactory.advanceTime(0.001); // resolve the first pose immediately
    const url = new URL(location.href);
    url.searchParams.set('anim', name);
    history.replaceState(null, '', url);
  };
  const startAnim = q.get('anim');
  animSel.value = startAnim && anims.includes(startAnim) ? startAnim : anims[0];
  animSel.onchange = () => playAnim(animSel.value);
  playAnim(animSel.value);

  // ---- auto-fit + zoom ----
  const b = display.getLocalBounds();
  const fitView = () => {
    const w = app.renderer.width, h = app.renderer.height;
    const fit = (Math.min(w, h) * 0.9) / Math.max(b.width, b.height);
    display.scale.set(fit);
    display.x = w / 2 - (b.x + b.width / 2) * fit;
    display.y = h / 2 - (b.y + b.height / 2) * fit;
  };
  fitView();
  window.addEventListener('resize', fitView);
  app.canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const k = Math.exp(-e.deltaY * 0.0015);
    // zoom around the cursor: keep the armature point under the mouse fixed
    display.x = e.clientX - (e.clientX - display.x) * k;
    display.y = e.clientY - (e.clientY - display.y) * k;
    display.scale.set(display.scale.x * k);
  }, { passive: false });
  app.canvas.addEventListener('dblclick', fitView);

  // ---- live clock ----
  app.ticker.add((t) => {
    if (!paused) PixiFactory.advanceTime((t.deltaMS / 1000) * speed);
  });

  // Hook for browser-automation checks (not a public API).
  (window as any).__app__ = app;
  (window as any).__display__ = display;
}

main().catch((e) => showError(String(e?.stack ?? e)));
