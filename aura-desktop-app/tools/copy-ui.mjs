// The pages' HTML and CSS beside the compiled scripts, in `dist/` (Tauri's
// `frontendDist`), and the console's own faces: its fonts.css and the Space Grotesk and
// JetBrains Mono files it serves, copied rather than kept twice in the repo. No
// bundler: the pages are a few small files each, and `withGlobalTauri` gives
// them the API without an import.
import { copyFileSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const app = join(dirname(fileURLToPath(import.meta.url)), '..');
const web = join(app, '..', 'aura-web');
const dist = join(app, 'dist');
mkdirSync(join(dist, 'fonts'), { recursive: true });
for (const name of readdirSync(join(app, 'ui'))) {
	if (name.endsWith('.html') || name.endsWith('.css')) copyFileSync(join(app, 'ui', name), join(dist, name));
}
copyFileSync(join(web, 'src', 'lib', 'styles', 'fonts.css'), join(dist, 'fonts.css'));
for (const name of readdirSync(join(web, 'static', 'fonts'))) {
	copyFileSync(join(web, 'static', 'fonts', name), join(dist, 'fonts', name));
}
