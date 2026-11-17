/** The slice of `window.__TAURI__` (withGlobalTauri) the app's pages use. */
interface TauriGlobal {
	core: { invoke<T = unknown>(command: string, args?: Record<string, unknown>): Promise<T> };
	event: { listen<T = unknown>(event: string, handler: (event: { payload: T }) => void): Promise<() => void> };
}
interface Window {
	__TAURI__: TauriGlobal;
}
