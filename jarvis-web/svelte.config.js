import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter({ out: 'build' }),
		csp: {
			mode: 'auto',
			directives: {
				'default-src': ['self'],
				// 'wasm-unsafe-eval' lets a script of this origin compile WebAssembly, and nothing
				// else (no eval, no other origin): the wake word's runtime is wasm (T-0875). Without
				// it the browser refused the module and the open mic fell back to every word.
				'script-src': ['self', 'wasm-unsafe-eval'],
				// GitLab's host, and only it: Devices → Get the apps reads the project's
				// releases there so the console can offer the phone and desktop builds
				// (ADR-0050). Without it the browser blocked the read and the section
				// always said "offline" -- which is what the legacy hud-apps spec caught
				// (e2e/v2-screens.spec.ts carries it now).
				// The origin is the build's (VITE_GITLAB_URL, T-0841), the one apps.ts reads.
				'connect-src': ['self', 'ws:', 'wss:', process.env.VITE_GITLAB_URL || 'https://gitlab.home.example'],
				'img-src': ['self', 'data:'],
				'style-src': ['self', 'unsafe-inline']
			}
		}
	}
};

export default config;
