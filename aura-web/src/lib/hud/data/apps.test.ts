import { describe, expect, it } from 'vitest';

import { GITLAB, STORE_URL, apkQr, latestApps, loadApps, storedApk } from './apps';

const pkg = (name: string) => `${GITLAB}/api/v4/projects/3/packages/generic/x/1/${name}`;

const releases = [
	{ tag_name: 'v1.0.1400+1400', released_at: '2026-09-24T16:00:00Z', description: 'Automatic build of feat at abc. **Test build.**', assets: { links: [{ name: 'jarvis-release.apk', url: pkg('t.apk') }] } },
	{ tag_name: 'v1.0.1395+1395', released_at: '2026-09-24T15:00:00Z', description: 'Jarvis `d99` from claude/jarvis-overhaul', assets: { links: [{ name: 'jarvis-release.apk', url: pkg('jarvis-release.apk') }, { name: 'adb-setup.txt', url: pkg('adb-setup.txt') }] } },
	{ tag_name: 'desktop-v1.0.1395', released_at: '2026-09-24T15:01:00Z', assets: { links: [{ name: 'Jarvis-1.0.1395.AppImage', url: pkg('a.AppImage') }, { name: 'Jarvis-1.0.1395.exe', url: pkg('a.exe') }] } },
	{ tag_name: 'v1.0.1207+1207', released_at: '2026-09-08T19:01:00Z', description: 'Jarvis old', assets: { links: [{ name: 'jarvis-release.apk', url: pkg('old.apk') }] } }
];

describe('the apps the console offers (ADR-0050)', () => {
	it('offers the newest production phone build, never a test build', () => {
		const { android } = latestApps(releases);
		expect(android?.version).toBe('1.0.1395');
		expect(android?.files).toEqual([{ label: 'Android APK', url: pkg('jarvis-release.apk') }]);
	});

	it('offers the newest desktop build, Linux and Windows', () => {
		const { desktop } = latestApps(releases);
		expect(desktop?.version).toBe('1.0.1395');
		expect(desktop?.files.map((f) => f.label)).toEqual(['Linux (AppImage)', 'Windows']);
	});

	it('labels the Tauri app\'s files (ADR-0051) the same way', () => {
		const tauri = [{ tag_name: 'desktop-v1.0.402', released_at: '2026-09-25T01:00:00Z', assets: { links: [{ name: 'Jarvis_1.0.402_amd64.AppImage', url: pkg('Jarvis_1.0.402_amd64.AppImage') }, { name: 'Jarvis_1.0.402_x64-setup.exe', url: pkg('Jarvis_1.0.402_x64-setup.exe') }] } }];
		expect(latestApps(tauri).desktop?.files.map((f) => f.label)).toEqual(['Linux (AppImage)', 'Windows']);
	});

	it('drops a link that is not https on the GitLab host', () => {
		const evil = [{ tag_name: 'v2+2', released_at: '2026-10-01', description: 'x', assets: { links: [{ name: 'jarvis.apk', url: 'https://example.com/jarvis.apk' }, { name: 'j.apk', url: 'http://gitlab.home.example/j.apk' }] } }];
		expect(latestApps(evil).android).toBeNull();
	});

	it('is empty rather than wrong on anything that is not a release list', () => {
		expect(latestApps(null)).toEqual({ android: null, desktop: null });
		expect(latestApps({ message: '401' })).toEqual({ android: null, desktop: null });
	});

	it('says offline when GitLab cannot be reached, and error when it answers badly', async () => {
		expect(await loadApps((async () => { throw new TypeError('fetch failed'); }) as typeof fetch)).toEqual({ status: 'offline' });
		expect(await loadApps((async () => new Response('', { status: 500 })) as typeof fetch)).toEqual({ status: 'error', detail: 'GitLab answered 500.' });
		const ok = await loadApps((async () => new Response(JSON.stringify(releases))) as typeof fetch);
		expect(ok.status === 'ready' && ok.apps.android?.version).toBe('1.0.1395');
	});

	it('says offline when GitLab never answers (a phone off the home network, CI with no route)', async () => {
		const hang = ((_u: string, init?: RequestInit) =>
			new Promise((_, reject) => init?.signal?.addEventListener('abort', () => reject(init.signal!.reason)))) as typeof fetch;
		expect(await loadApps(hang, 10)).toEqual({ status: 'offline' });
	});

	// The owner, 2026-11-08: "how can I access the artifact you uploaded to jarvis directly, and
	// how can we have the download behaviour work with either one" (T-0735).
	const id = '6ae7aa8eb31e36eca029337de92647d693c99fbcff7ddda50e2663fdaeddc486';
	const signed = `https://jarvis.example.test/hermes/api/artifacts/${id}?exp=1&sig=ab`;
	const stored = (title: string, extra: Record<string, unknown> = {}) => ({
		id, title, mime: 'application/vnd.android.package-archive', created: 1794165888, signed_url: signed, ...extra
	});

	it('offers a phone build from the Jarvis store, its version read from its title', () => {
		expect(storedApk({ artifacts: [stored('Jarvis 1.0.1672.apk'), stored('Jarvis 1.0.1673 (v2 popup).apk')] })).toMatchObject({
			version: '1.0.1673', tag: 'v1.0.1673+1673', from: 'the house', files: [{ url: signed }]
		});
		expect(storedApk({ artifacts: [stored('car log headway-1.txt', { mime: 'text/plain' })] })).toBeNull();
		expect(storedApk({ artifacts: [stored('Jarvis 1.0.1673.apk', { signed_url: 'javascript:alert(1)' })] })).toBeNull();
		expect(storedApk({ artifacts: [stored('Jarvis 1.0.1673.apk', { signed_url: 'https://evil.test/x.apk' })] })).toBeNull();
		expect(storedApk([stored('Jarvis 1.0.1673.apk')])).toBeNull();
	});

	it('offers the newer of GitLab\'s and Jarvis\'s phone builds, and the store even when GitLab is unreachable', async () => {
		const both = (gitlab: unknown, store: unknown) =>
			(async (url: string) => (url === STORE_URL ? new Response(JSON.stringify(store)) : gitlab instanceof Error ? Promise.reject(gitlab) : new Response(JSON.stringify(gitlab)))) as typeof fetch;
		const newer = await loadApps(both(releases, { artifacts: [stored('Jarvis 1.0.1673.apk')] }));
		expect(newer.status === 'ready' && newer.apps.android?.from).toBe('the house');
		expect(newer.status === 'ready' && newer.apps.desktop).toBeTruthy();
		const older = await loadApps(both(releases, { artifacts: [stored('Jarvis 0.0.1.apk')] }));
		expect(older.status === 'ready' && older.apps.android?.from).toBeUndefined();
		const off = await loadApps(both(new TypeError('fetch failed'), { artifacts: [stored('Jarvis 1.0.1673.apk')] }));
		expect(off).toMatchObject({ status: 'ready', apps: { android: { version: '1.0.1673' }, desktop: null } });
	});

	it('draws the APK link as a scannable QR image', () => {
		expect(apkQr(pkg('jarvis-release.apk'))).toMatch(/^data:image\/svg\+xml;utf8,%3Csvg/);
		expect(decodeURIComponent(apkQr(pkg('jarvis-release.apk')))).toContain('Download link for the phone app');
	});
});

describe('the console may read GitLab and nothing else off its own origin (ADR-0050)', () => {
	it('connect-src names the GitLab host the releases come from, and no wildcard https', async () => {
		const { default: config } = await import('../../../../svelte.config.js');
		const connect = (config.kit?.csp?.directives?.['connect-src'] ?? []) as string[];
		expect(connect).toContain(GITLAB);
		expect(connect).not.toContain('https:');
		expect(connect.filter((s) => s.startsWith('https://'))).toEqual([GITLAB]);
	});
});
