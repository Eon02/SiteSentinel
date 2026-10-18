// Runs after `next build` (npm "postbuild"). Lists every file of this build's /_next/static
// output in public/sw-manifest.json, so the service worker (public/sw.js) can cache all of the
// app's code up front, including chunks that are only loaded on demand (e.g. the report form's
// location map). Without this, those chunks would be missing offline and crash the page.
import { createHash } from 'node:crypto';
import { readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const root = join(process.cwd(), '.next', 'static');
const assets = [];
let bytes = 0;

(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) walk(path);
    else if (!name.endsWith('.map')) {
      assets.push('/_next/static/' + relative(root, path).split(sep).join('/'));
      bytes += stat.size;
    }
  }
})(root);

assets.sort();
const version = createHash('sha256').update(assets.join('\n')).digest('hex').slice(0, 12);
writeFileSync(join(process.cwd(), 'public', 'sw-manifest.json'), JSON.stringify({ version, assets }, null, 1));
console.log(`sw-manifest.json: ${assets.length} files, ${(bytes / 1024 / 1024).toFixed(1)} MB, version ${version}`);
