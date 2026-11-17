// The model manager's client: the models feed's loader and the Models screen's actions.
//
// Every call goes through the console's relay (`/hermes/api/models`), which
// holds the adapter's key, and every refusal is the manager's own sentence
// (`{ok: false, error}`), shown as it came: "not enough disk" and "gated" are
// the manager's words, not ours.
//
// Does not cover: checking what it sends. The screen builds each body with the
// mappers in data/models.ts (addBody, settingsPatch) and the manager checks it
// again; nothing here retries, so a write that failed is said once and left.

import { ADAPTER_AWAY, type AddBody, type ApiGpu, type ApiLanding, type ApiList, type ApiSearchResult, type ModelSettings, type ModelsData } from './data/models';

const BASE = '/hermes/api/models';

async function call<T>(method: string, path = '', body?: unknown): Promise<T> {
	let res: Response;
	try {
		res = await fetch(`${BASE}${path}`, {
			method,
			headers: { accept: 'application/json', ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
	} catch {
		throw new Error(ADAPTER_AWAY);
	}
	if (res.status === 502 || res.status === 503) throw new Error(ADAPTER_AWAY);
	const json = (await res.json().catch(() => null)) as ({ ok?: boolean; error?: string } & T) | null;
	if (!res.ok || !json || json.ok === false) throw new Error(json?.error || `The model manager answered ${res.status}.`);
	return json;
}

/** The feed's loader: both lists in one go, so a card and its GPU bar never disagree by a poll. */
export async function loadModels(): Promise<ModelsData> {
	const [list, gpus] = await Promise.all([call<ApiList>('GET'), call<{ gpus: ApiGpu[] }>('GET', '/gpus')]);
	return { list, gpus: gpus.gpus };
}

// An id reaches the path encoded: the manager's ids cannot hold a slash, but a list that
// lied about one must not turn a load into a call to another route.
const at = (id: string, rest = ''): string => `/${encodeURIComponent(id)}${rest}`;

export const searchModels = async (q: string): Promise<ApiSearchResult[]> =>
	(await call<{ results?: ApiSearchResult[] }>('GET', `/search?q=${encodeURIComponent(q)}`)).results ?? [];
/** Starts a download: the whole repo, or the one .gguf of it a llama.cpp model serves. */
export const downloadModel = (repo: string, file?: string): Promise<unknown> => call('POST', '/downloads', file ? { repo, file } : { repo });
export const createModel = (body: AddBody): Promise<unknown> => call('POST', '', body);
export const updateModel = (id: string, settings: Partial<ModelSettings>): Promise<unknown> => call('PATCH', at(id), { settings });
/** Removes the model; with `weights`, its files too, unless another model serves the same repo. */
export const deleteModel = (id: string, weights: boolean): Promise<unknown> => call('DELETE', at(id, weights ? '?weights=1' : ''));
/** Asks for the load and answers the cards it lands on; the model is `starting` until a later poll. */
export const loadModel = async (id: string): Promise<ApiLanding[]> => (await call<{ gpus?: ApiLanding[] }>('POST', at(id, '/load'))).gpus ?? [];
export const unloadModel = (id: string): Promise<unknown> => call('POST', at(id, '/unload'));
