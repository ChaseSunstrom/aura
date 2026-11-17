/**
 * The phone and desktop apps, as the console offers them for download (ADR-0050).
 *
 * Read from the project's GitLab releases -- the same feed the installed apps update
 * from, so the console never offers a build the apps would not also move to. GitLab
 * answers this API to any origin, so the browser reads it directly over the home network.
 *
 * Only a production build is offered for the phone: a release whose notes open with
 * "Automatic build" is a test build, which the phone takes only on opt-in
 * (`ReleaseFeed.kt` reads the same words). A link is kept only if it is https on the
 * GitLab host itself: a release's links are text someone could edit, and a download
 * button pointing wherever that text said is the updater's worst failure, repeated here.
 */

import { qrSvg } from '../../qr';

/** The house's GitLab. The repo names no house domain (T-0841): the image build passes its own
 * (VITE_GITLAB_URL, from CI's server or the host's .env) and svelte.config.js allows the same
 * origin; a build without it reads the placeholder and the section says it is offline. */
export const GITLAB: string = import.meta.env?.VITE_GITLAB_URL || 'https://gitlab.home.example';
export const RELEASES_URL = `${GITLAB}/api/v4/projects/3/releases?per_page=40`;

export interface AppFile {
	label: string;
	url: string;
}

export interface AppBuild {
	version: string;
	tag: string;
	released: string;
	files: AppFile[];
	/** Where a build that is not one of GitLab's releases lives: 'Jarvis', its artifact store. */
	from?: string;
}

export interface Apps {
	android: AppBuild | null;
	desktop: AppBuild | null;
}

interface Release {
	tag_name?: unknown;
	released_at?: unknown;
	description?: unknown;
	assets?: { links?: { name?: unknown; url?: unknown }[] };
}

const onGitlab = (url: string): boolean => {
	try {
		const u = new URL(url);
		return u.protocol === 'https:' && u.host === new URL(GITLAB).host;
	} catch {
		return false;
	}
};

function files(r: Release, pick: (name: string) => string | null): AppFile[] {
	const out: AppFile[] = [];
	for (const link of r.assets?.links ?? []) {
		const name = typeof link.name === 'string' ? link.name : '';
		const url = typeof link.url === 'string' ? link.url : '';
		const label = pick(name);
		if (label && onGitlab(url)) out.push({ label, url });
	}
	return out;
}

/** The newest production phone build and the newest desktop build in `releases`. */
export function latestApps(releases: unknown): Apps {
	const list = (Array.isArray(releases) ? releases : []) as Release[];
	const byDate = [...list].sort((a, b) => String(b.released_at ?? '').localeCompare(String(a.released_at ?? '')));
	let android: AppBuild | null = null;
	let desktop: AppBuild | null = null;
	for (const r of byDate) {
		const tag = typeof r.tag_name === 'string' ? r.tag_name : '';
		const released = typeof r.released_at === 'string' ? r.released_at : '';
		if (!android && /^v\d/.test(tag) && !String(r.description ?? '').trimStart().startsWith('Automatic build')) {
			const apk = files(r, (n) => (n.endsWith('.apk') ? 'Android APK' : null));
			if (apk.length) android = { version: tag.replace(/^v/, '').replace(/\+.*$/, ''), tag, released, files: apk };
		}
		if (!desktop && tag.startsWith('desktop-v')) {
			const dist = files(r, (n) => (n.endsWith('.AppImage') ? 'Linux (AppImage)' : n.endsWith('.exe') ? 'Windows' : null));
			if (dist.length) desktop = { version: tag.replace(/^desktop-v/, ''), tag, released, files: dist };
		}
		if (android && desktop) break;
	}
	return { android, desktop };
}

/**
 * The Jarvis store's phone builds (T-0735), through this console's own adapter proxy: a
 * hand-built APK is put there, and the phone's updater reads the same listing
 * (`ReleaseFeed.pickStored`). A row is a build when it is an APK titled `Jarvis <version>`.
 */
export const STORE_URL = '/hermes/api/artifacts?kind=application/vnd.android.package-archive';
const STORED_TITLE = /^Jarvis v?(\d+\.\d+\.(\d+))\b/;

/** The build's versionCode: after the tag's `+`, else the version's last number. */
const codeOf = (b: AppBuild): number => Number(b.tag.split('+')[1] ?? b.version.split('.').at(-1)) || 0;

/**
 * The newest phone build in the adapter's artifact listing, or null. Its link is the
 * listing's signed URL (good for an hour, so a phone's camera can open it without the key),
 * kept only when it is http(s) and names an artifact: the same refusal GitLab's links get.
 */
export function storedApk(listing: unknown): AppBuild | null {
	const rows = (listing as { artifacts?: unknown } | null)?.artifacts;
	let best: AppBuild | null = null;
	for (const row of Array.isArray(rows) ? (rows as Record<string, unknown>[]) : []) {
		const match = row.mime === 'application/vnd.android.package-archive' && typeof row.title === 'string' ? STORED_TITLE.exec(row.title) : null;
		const url = typeof row.signed_url === 'string' ? row.signed_url : '';
		if (!match || !anArtifact(url)) continue;
		const build: AppBuild = {
			version: match[1],
			tag: `v${match[1]}+${match[2]}`,
			released: typeof row.created === 'number' ? new Date(row.created * 1000).toISOString() : '',
			files: [{ label: 'Android APK', url }],
			// Named without the persona: e2e specs import this module in plain Node, where the
			// theme store (Svelte runes) can't load.
			from: 'the house'
		};
		if (!best || codeOf(build) > codeOf(best)) best = build;
	}
	return best;
}

function anArtifact(url: string): boolean {
	try {
		const u = new URL(url);
		return (u.protocol === 'https:' || u.protocol === 'http:') && /\/api\/artifacts\/[0-9a-f]{64}$/.test(u.pathname);
	} catch {
		return false;
	}
}

/** A QR of the APK's own download link, as an image the phone's camera can open. */
export function apkQr(url: string): string {
	return `data:image/svg+xml;utf8,${encodeURIComponent(qrSvg(url, { title: 'Download link for the phone app' }))}`;
}

export type AppsLoad =
	| { status: 'loading' }
	| { status: 'ready'; apps: Apps }
	| { status: 'error'; detail: string }
	| { status: 'offline' };

/** Fetch and pick. A network failure off the home network is `offline`, not an error to fix. */
// Off the home network GitLab's private address is never refused, only unanswered, and the
// browser's own connect timeout is minutes: without a bound the panel sat on its skeleton
// and Playwright's networkidle never came (hud-a11y timed out on every settings route).
export async function loadApps(fetchImpl: typeof fetch = fetch, timeoutMs = 8000): Promise<AppsLoad> {
	// Either source can carry the newest phone build; GitLab out of reach still leaves Jarvis's.
	const [gitlab, stored] = await Promise.all([loadReleases(fetchImpl, timeoutMs), loadStored(fetchImpl, timeoutMs)]);
	if (!stored) return gitlab;
	const apps = gitlab.status === 'ready' ? gitlab.apps : { android: null, desktop: null };
	return { status: 'ready', apps: { ...apps, android: apps.android && codeOf(apps.android) >= codeOf(stored) ? apps.android : stored } };
}

async function loadStored(fetchImpl: typeof fetch, timeoutMs: number): Promise<AppBuild | null> {
	try {
		const res = await fetchImpl(STORE_URL, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(timeoutMs) });
		return res.ok ? storedApk(await res.json()) : null;
	} catch {
		return null;
	}
}

async function loadReleases(fetchImpl: typeof fetch, timeoutMs: number): Promise<AppsLoad> {
	try {
		const res = await fetchImpl(RELEASES_URL, {
			headers: { Accept: 'application/json' },
			signal: AbortSignal.timeout(timeoutMs)
		});
		if (!res.ok) return { status: 'error', detail: `GitLab answered ${res.status}.` };
		return { status: 'ready', apps: latestApps(await res.json()) };
	} catch {
		return { status: 'offline' };
	}
}
