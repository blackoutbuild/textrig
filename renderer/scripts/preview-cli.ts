// `pnpm preview [name]` — live preview of a generated rig triple in the
// browser (see preview.html / previewEntry.ts). If our dev server is already
// running on :3020 it is reused (open URL, exit); otherwise start it and stay
// alive so the auto-reload watcher keeps working while rigs are rebuilt.
import { exec } from 'child_process';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import { createServer } from 'vite';

const rendererDir = resolve(fileURLToPath(import.meta.url), '../..');
const PORT = 3020;
const name = process.argv[2];

async function rigsFrom(base: string): Promise<string[] | null> {
  try {
    const r = await fetch(`${base}/rigs`, { signal: AbortSignal.timeout(1000) });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

const openInBrowser = (url: string) => {
  console.log(`preview: ${url}`);
  if (process.platform === 'darwin') exec(`open ${JSON.stringify(url)}`);
};

let base = `http://localhost:${PORT}`;
let rigs = await rigsFrom(base); // non-null ⇒ our server already runs there

if (rigs === null) {
  const server = await createServer({ configFile: resolve(rendererDir, 'vite.config.ts'), root: rendererDir });
  await server.listen();
  base = server.resolvedUrls!.local[0].replace(/\/$/, '');
  rigs = (await rigsFrom(base)) ?? [];
  console.log('dev server started (Ctrl-C to stop; page auto-reloads on rig rebuild)');
} else {
  console.log(`reusing dev server on :${PORT}`);
}

if (name && !rigs.includes(name)) {
  console.warn(`warning: rig "${name}" has no complete triple in out/ (available: ${rigs.join(', ') || 'none'})`);
}
const target = name ?? rigs[0];
openInBrowser(`${base}/preview.html${target ? `?name=${encodeURIComponent(target)}` : ''}`);
