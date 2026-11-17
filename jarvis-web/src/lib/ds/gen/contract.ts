// What the model may project, read from the contract and nowhere else.
//
// `projections.json` here is the console's copy of
// `tests/contracts/projections.json` (the build sees only jarvis-web; the
// copy's test fails when the two differ). The jarvis-hud plugin validates
// `hud_project` against the same file with the same messages, so a payload the
// model was told is fine is drawn, and one it was refused never arrives.
//
// Does not cover: what a string says. Every string is drawn as text (never
// `{@html}`); custom_html's is run, but only inside the sandboxed frame.

import contract from './projections.json';

type Kind = 'string' | 'number' | 'integer' | 'boolean' | 'array' | 'object';

export interface FieldSpec {
	type: Kind | Kind[];
	required?: boolean;
	chars?: number;
	min?: number;
	max?: number;
	enum?: string[];
	pattern?: string;
	prefix?: string[];
	items?: FieldSpec;
	fields?: Record<string, FieldSpec>;
}

interface TypeSpec {
	about: string;
	empty_when: string[];
	fields: Record<string, FieldSpec>;
}

export const CONTRACT = contract as unknown as {
	event: string;
	origin: string;
	limits: { data_bytes: number; id_pattern: string; custom_html_seconds: number };
	tones: string[];
	common_fields: Record<string, FieldSpec>;
	types: Record<string, TypeSpec>;
	examples: Record<string, unknown>;
	malformed: { type: string; data: unknown; error: string }[];
};

export const PROJECTION_TYPES: readonly string[] = Object.keys(CONTRACT.types);

/** What the projection layer passes `Projected` beyond the data: where it sits, whether it goes. */
export interface Placement {
	/** The core's centre in viewport pixels: the projection grows out of it and goes back into it. */
	core?: { x: number; y: number };
	/** An edge line on the side it butts against. */
	edge?: 'top' | 'right' | 'bottom' | 'left';
	/** Going back into the core. custom_html's frame is removed the moment this turns true. */
	leaving?: boolean;
	/** The stage's `view().from`, so an interrupted exit turns round. */
	from?: number;
}

/** One `hud.projection` frame's envelope. */
export interface ProjectionFrame {
	id: string;
	type: string;
	data: unknown;
}

const NAMES: Record<Kind, string> = {
	string: 'a string',
	number: 'a number',
	integer: 'an integer',
	boolean: 'true or false',
	array: 'an array',
	object: 'an object'
};

const ID = new RegExp(CONTRACT.limits.id_pattern);

const isObject = (v: unknown): v is Record<string, unknown> =>
	typeof v === 'object' && v !== null && !Array.isArray(v);

function is(v: unknown, kind: Kind): boolean {
	switch (kind) {
		case 'string':
			return typeof v === 'string';
		case 'number':
			return typeof v === 'number' && Number.isFinite(v);
		case 'integer':
			return typeof v === 'number' && Number.isInteger(v);
		case 'boolean':
			return typeof v === 'boolean';
		case 'array':
			return Array.isArray(v);
		case 'object':
			return isObject(v);
	}
}

const items = (n: number) => `${n} item${n === 1 ? '' : 's'}`;
const join = (path: string, key: string) => (path ? `${path}.${key}` : key);

function check(v: unknown, spec: FieldSpec, path: string, type: string): string | null {
	const kinds = Array.isArray(spec.type) ? spec.type : [spec.type];
	const kind = kinds.find((k) => is(v, k));
	if (!kind) return `${path} must be ${kinds.map((k) => NAMES[k]).join(' or ')}`;
	if (typeof v === 'string') {
		if (spec.enum && !spec.enum.includes(v)) return `${path} must be one of ${spec.enum.join(', ')}`;
		if (spec.pattern && !new RegExp(spec.pattern).test(v)) return `${path} has characters it may not`;
		if (spec.prefix && !spec.prefix.some((p) => v.startsWith(p))) {
			return `${path} must start with ${spec.prefix.join(' or ')}`;
		}
		// UTF-16 units, which is what `.length` counts and what the plugin counts too.
		if (spec.chars !== undefined && v.length > spec.chars) {
			return `${path} is longer than ${spec.chars} characters`;
		}
		return null;
	}
	if (typeof v === 'number') {
		if (spec.min !== undefined && v < spec.min) return `${path} must be at least ${spec.min}`;
		if (spec.max !== undefined && v > spec.max) return `${path} must be at most ${spec.max}`;
		return null;
	}
	if (Array.isArray(v)) {
		if (spec.max !== undefined && v.length > spec.max) return `${path} has more than ${items(spec.max)}`;
		if (spec.min !== undefined && v.length < spec.min) return `${path} needs at least ${items(spec.min)}`;
		if (spec.items) {
			for (let i = 0; i < v.length; i++) {
				const error = check(v[i], spec.items, `${path}[${i}]`, type);
				if (error) return error;
			}
		}
		return null;
	}
	if (isObject(v) && spec.fields) return checkFields(v, spec.fields, path, type);
	return null;
}

function checkFields(
	obj: Record<string, unknown>,
	fields: Record<string, FieldSpec>,
	path: string,
	type: string
): string | null {
	// Unknown keys first: a misspelt field is the likeliest mistake and the clearest to fix.
	for (const key of Object.keys(obj)) {
		if (!Object.hasOwn(fields, key)) return `${join(path, key)} is not a field of ${type}`;
	}
	for (const [key, spec] of Object.entries(fields)) {
		if (!Object.hasOwn(obj, key) || obj[key] === undefined) {
			if (spec.required) return `${join(path, key)} is required`;
			continue;
		}
		const error = check(obj[key], spec, join(path, key), type);
		if (error) return error;
	}
	return null;
}

/**
 * The first thing wrong with a projection, in the plugin's words, or null when
 * it can be drawn. The size cap is JSON's UTF-8 length; at the boundary the two
 * languages' escaping can differ by a few bytes, which only moves where a
 * near-cap payload is refused.
 */
export function validateProjection(type: unknown, data: unknown): string | null {
	if (typeof type !== 'string' || !Object.hasOwn(CONTRACT.types, type)) {
		return `${String(type).slice(0, 40)} is not a projection type`;
	}
	if (!isObject(data)) return 'data must be an object';
	let bytes: number;
	try {
		bytes = new TextEncoder().encode(JSON.stringify(data)).length;
	} catch {
		return 'data must be plain JSON';
	}
	if (bytes > CONTRACT.limits.data_bytes) return `data is larger than ${CONTRACT.limits.data_bytes} bytes`;
	return checkFields(data, { ...CONTRACT.common_fields, ...CONTRACT.types[type].fields }, '', type);
}

/** Whether a valid projection has nothing to draw: every `empty_when` field absent, '' or []. */
export function isEmptyProjection(type: string, data: unknown): boolean {
	const spec = Object.hasOwn(CONTRACT.types, type) ? CONTRACT.types[type] : undefined;
	if (!spec || !isObject(data)) return false;
	return spec.empty_when.every((key) => {
		const v = data[key];
		return v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
	});
}

/**
 * A `hud.projection` frame's payload as the projection layer files it, or null
 * when it has no usable id or type (there is nothing to replace or show). The
 * data is left as it came: `Projected` validates it and shows the error.
 */
export function projectionFromEvent(payload: unknown): ProjectionFrame | null {
	if (!isObject(payload)) return null;
	const { id, type, data } = payload;
	if (typeof id !== 'string' || !ID.test(id)) return null;
	if (typeof type !== 'string' || !type) return null;
	return { id, type, data };
}
