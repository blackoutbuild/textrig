// Headless DragonBones renderer: programmatic Vite dev server + headless
// Chromium (playwright), __DONE__/__ERROR__ window protocol, and a try/finally
// that closes the browser AND the server on every exit path so no Chromium leaks.
import { writeFileSync } from 'fs';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const rendererDir = resolve(fileURLToPath(import.meta.url), '../..');
const repoRoot = resolve(rendererDir, '..');

// usage: pnpm render <assetPrefix> [animName] [outName] [zoom] [fx] [fy] [--flags]
//   pnpm render out:robot idle robot --match-runtime --frames 8 --out-repo
//       → read the GENERATED out/robot_*.json triple, write
//         out/robot.filmstrip.png (and out/robot.gif with --gif).
//   pnpm render figure2/figure2 wave figure2
//       → render a committed test triple under assets/.
//   zoom/fx/fy zoom into the armature (fx/fy: 0=left/top of bounds),
//   e.g. face close-up: 3 0.8 0.12
// Flags: --frames N  --size N  --span S  --match-runtime (evenly spaced loop sampling)
//        --gif (also write a full-loop GIF)
//        --out-repo (write into the repo out/ dir, not renderer/out/)
const raw = process.argv.slice(2);
const flags = new Set<string>();
const flagVal: Record<string, string> = {};
const pos: string[] = [];
for (let i = 0; i < raw.length; i++) {
  const a = raw[i];
  if (a === '--match-runtime' || a === '--out-repo' || a === '--gif') flags.add(a);
  else if (a.startsWith('--')) { flagVal[a.slice(2)] = raw[++i]; }
  else pos.push(a);
}
const [assetArg, animArg, outArg, zoomArg, fxArg, fyArg] = pos;
if (!assetArg) {
  console.error('usage: pnpm render <out:name | assets-prefix> [anim] [outName] [zoom] [fx] [fy] [--frames N] [--size N] [--gif] [--match-runtime] [--out-repo]');
  process.exit(2);
}
const outName = outArg ?? assetArg.replace(/^out:/, '').replace(/\//g, '-');
const outDir = flags.has('--out-repo') ? resolve(repoRoot, 'out') : resolve(rendererDir, 'out');
const assetPath = assetArg.startsWith('out:') ? '/out/' + assetArg.slice(4) : '/assets/' + assetArg;

const server = await createServer({ configFile: resolve(rendererDir, 'vite.config.ts'), root: rendererDir });
await server.listen();
const base = server.resolvedUrls!.local[0];

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  page.on('console', (m) => console.log('[page]', m.text()));
  page.on('pageerror', (e) => console.error('[page error]', e.message));
  const params = new URLSearchParams();
  params.set('asset', assetPath);
  if (animArg) params.set('anim', animArg);
  if (zoomArg) params.set('zoom', zoomArg);
  if (fxArg) params.set('fx', fxArg);
  if (fyArg) params.set('fy', fyArg);
  if (flags.has('--match-runtime')) params.set('sample', 'fraction');
  if (flagVal.frames) params.set('frames', flagVal.frames);
  if (flagVal.size) params.set('size', flagVal.size);
  if (flagVal.span) params.set('span', flagVal.span);
  if (flags.has('--gif')) params.set('gif', '1');
  const qs = params.toString();
  await page.goto(base + 'render.html' + (qs ? '?' + qs : ''));
  await page.waitForFunction(() => (window as any).__DONE__ || (window as any).__ERROR__, undefined, { timeout: 60000 });

  const err = await page.evaluate(() => (window as any).__ERROR__);
  if (err) {
    console.error('SPIKE FAILED:\n' + err);
    process.exitCode = 1;
  } else {
    const result = (await page.evaluate(() => (window as any).__RESULT__)) as {
      filmstrip: string; gif?: string; checksums: number[]; armature: string; anim: string; durationS: number;
      meshCount: number; meshVertexChecksums: number[];
    };
    writeFileSync(resolve(outDir, `${outName}.filmstrip.png`), Buffer.from(result.filmstrip.split(',')[1], 'base64'));
    if (result.gif) {
      writeFileSync(resolve(outDir, `${outName}.gif`), Buffer.from(result.gif, 'base64'));
      console.log(`wrote out/${outName}.gif`);
    }
    const uniq = new Set(result.checksums).size;
    console.log(`armature: ${result.armature}  anim: ${result.anim}  duration: ${result.durationS.toFixed(3)}s`);
    console.log(`checksums: ${result.checksums.join(', ')}`);
    console.log(`distinct frames: ${uniq}/${result.checksums.length}`);
    const mvUniq = new Set(result.meshVertexChecksums).size;
    console.log(`mesh render-objects: ${result.meshCount}  vertex-buffer checksums: ${result.meshVertexChecksums.join(', ')}`);
    console.log(`distinct mesh vertex states: ${mvUniq}/${result.meshVertexChecksums.length}` +
      (result.meshCount > 0 ? (mvUniq > 1 ? '  → vertices DEFORM (skinning/FFD active)' : '  ⚠ mesh present but vertices static') : '  (no meshes in asset)'));
    console.log(`wrote out/${outName}.filmstrip.png`);
    const moved = uniq > 1;
    const nonBlank = result.checksums.some((c) => c !== result.checksums[0]) || result.checksums[0] !== 0;
    if (!moved) console.error('VERDICT RISK: animation did not move (all frame checksums identical)');
    if (!nonBlank) console.error('VERDICT RISK: frames appear blank');
    process.exitCode = moved && nonBlank ? 0 : 1;
  }
} catch (e) {
  console.error('render failed:', e);
  process.exitCode = 1;
} finally {
  await browser.close();
  await server.close();
}
