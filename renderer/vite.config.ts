import { defineConfig } from 'vite';
import { dirname, resolve, sep } from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';

const root = dirname(fileURLToPath(import.meta.url));
const assetsDir = resolve(root, 'assets');
// Repo out/ dir: the pipeline's `build_rig.sh --target db` writes generated
// <name>_ske.json/_tex.json/_tex.png triples here. Serving it lets the runner
// read generated rigs straight from out/ with NO manual copy into assets/.
const outDir = resolve(root, '../out');

// A static-file middleware factory: serve `<baseDir>/<path>` with an explicit
// mime type so a raw fetch() (json) and Pixi Assets.load() (png) both get the
// file verbatim — Vite would otherwise treat a fetched .json as a JS module.
const staticDir = (baseDir: string) => (req: any, res: any, next: () => void) => {
  const rel = (req.url ?? '/').split('?')[0];
  const p = resolve(baseDir, '.' + rel);
  if ((p !== baseDir && !p.startsWith(baseDir + sep)) || !fs.existsSync(p) || fs.statSync(p).isDirectory())
    return next();
  const ext = p.split('.').pop();
  const mime = ext === 'json' ? 'application/json' : ext === 'png' ? 'image/png' : 'application/octet-stream';
  res.setHeader('Content-Type', mime);
  fs.createReadStream(p).on('error', () => { res.statusCode = 500; res.end(); }).pipe(res);
};

// Rigs = names with a complete generated triple in out/.
const listRigs = () =>
  fs.readdirSync(outDir)
    .filter((f) => f.endsWith('_ske.json'))
    .map((f) => f.slice(0, -'_ske.json'.length))
    .filter((n) => fs.existsSync(resolve(outDir, `${n}_tex.json`)) && fs.existsSync(resolve(outDir, `${n}_tex.png`)))
    .sort();

// <name>_ske.json | <name>_tex.json | <name>_tex.png → <name>, else null.
const rigNameOf = (file: string) => {
  const m = /^(.+)_(ske\.json|tex\.json|tex\.png)$/.exec(file);
  return m ? m[1] : null;
};

export default defineConfig({
  server: { port: 3020 },
  plugins: [
    {
      name: 'serve-assets',
      configureServer(server) {
        server.middlewares.use('/assets', staticDir(assetsDir)); // committed known-good triples
        server.middlewares.use('/out', staticDir(outDir));       // pipeline-generated rigs
        server.middlewares.use('/rigs', (_req, res) => {
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify(listRigs()));
        });
        // Markup-review mailbox (markup.html "Done" button): atomically write
        // the posted JSON to out/markup-<name>.result.json where the MCP
        // await_markup_review tool polls for it. A file mailbox, no logic;
        // body capped at 10 MB (markup JSON is KB-scale — anything bigger is a bug).
        server.middlewares.use('/markup-result', (req: any, res: any, next: () => void) => {
          if (req.method !== 'POST') return next();
          const m = /^\/([\w-]+)$/.exec((req.url ?? '').split('?')[0]);
          if (!m) { res.statusCode = 400; return res.end('bad markup name'); }
          const chunks: Buffer[] = [];
          let size = 0;
          req.on('data', (c: Buffer) => {
            size += c.length;
            if (size > 10 * 1024 * 1024) { res.statusCode = 413; res.end('too large'); req.destroy(); return; }
            chunks.push(c);
          });
          req.on('end', () => {
            const body = Buffer.concat(chunks);
            try { JSON.parse(body.toString('utf8')); }
            catch { res.statusCode = 400; return res.end('malformed json'); }
            const tmp = resolve(outDir, `markup-${m[1]}.result.json.tmp`);
            fs.writeFileSync(tmp, body);
            fs.renameSync(tmp, resolve(outDir, `markup-${m[1]}.result.json`));
            res.statusCode = 204; res.end();
          });
        });
        // Live-preview auto-reload: a build_rig.sh run rewrites the three triple
        // files in close succession — debounce per rig, then tell the page.
        server.watcher.add(outDir);
        const pending = new Map<string, ReturnType<typeof setTimeout>>();
        const onFile = (p: string) => {
          if (!p.startsWith(outDir + sep)) return;
          const name = rigNameOf(p.slice(outDir.length + 1));
          if (!name) return;
          clearTimeout(pending.get(name));
          pending.set(name, setTimeout(() => {
            pending.delete(name);
            server.ws.send({ type: 'custom', event: 'rig-rebuilt', data: { name } });
          }, 300));
        };
        server.watcher.on('add', onFile);
        server.watcher.on('change', onFile);
      },
    },
  ],
});
