// Defect-only ESLint for the console (eslint 9 flat config).
//
// Same stance as the repo's `ruff.toml`: this gate catches bugs, not style. No
// stylistic or formatting rules live here — tabs/quotes/semicolons are matched
// by hand against the file, and `svelte-check` already owns Svelte's a11y and
// unused-CSS warnings, so those are not duplicated here.
//
// Requires (devDependencies of jarvis-web): eslint, @eslint/js,
// typescript-eslint, eslint-plugin-svelte (which brings svelte-eslint-parser).
import js from '@eslint/js';
import ts from 'typescript-eslint';
import svelte from 'eslint-plugin-svelte';

export default ts.config(
	js.configs.recommended,
	...ts.configs.recommended, // NOT recommendedTypeChecked: no type-aware pass, so no build is needed and the gate stays fast.
	...svelte.configs['flat/recommended'],
	{
		languageOptions: { parserOptions: { extraFileExtensions: ['.svelte'] } }
	},
	{
		// `.svelte.ts`/`.svelte.js` are Svelte 5 rune modules: svelte-eslint-parser handles them,
		// but only reaches the TypeScript inside when handed the TS parser as its sub-parser.
		files: ['**/*.svelte', '**/*.svelte.ts', '**/*.svelte.js'],
		languageOptions: { parserOptions: { parser: ts.parser } }
	},
	{
		rules: {
			// Silence the defaults that fight this codebase's deliberate patterns —
			// each with a reason, the way `ruff.toml` curates its set.
			'@typescript-eslint/no-explicit-any': 'off', // Hermes payloads are `any` at the wire boundary; typing them would be a lie.
			'@typescript-eslint/no-unused-vars': [
				'error',
				{ argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' }
			],
			'no-undef': 'off', // TypeScript owns undefined-symbol; this rule only adds env-globals noise on a TS codebase.
			// svelte-check is the Svelte compiler gate (a11y, unused CSS, valid-compile);
			// leaving those on here would double-report every warning it already owns.
			'svelte/valid-compile': 'off',
			// Stylistic, not a defect: the console deliberately writes explicit `{#snippet children()}`
			// wrappers (the `ScreenState` docstring documents the pattern) for parity with its named
			// snippets. This rule would rewrite ~70 deliberate call sites for verbosity alone.
			'svelte/no-useless-children-snippet': 'off',
			// The console is served at the root by adapter-node with no `paths.base`, so `resolve()`
			// is a no-op here and absolute root-relative links are the deliberate, repo-wide pattern.
			// Re-enable this if the console ever gains a base path.
			'svelte/no-navigation-without-resolve': 'off',
			// Fires on the console's "build a fresh Map/Set/Date locally, publish it whole" pattern
			// (inside `$derived.by`, in copy-on-write handlers, on a mirror commented as non-reactive)
			// and cannot tell it from the real bug — mutating a built-in after it was published.
			// Measured: 11 sites flagged, 0 of them the bug. A recommendation, not a defect rule.
			'svelte/prefer-svelte-reactivity': 'off',
			// A refactor suggestion (`$state` reset by an `$effect` → writable `$derived`): the two
			// spellings behave the same for the person, so this is style, not a defect.
			'svelte/prefer-writable-derived': 'off',
			'svelte/no-at-html-tags': 'error' // XSS-shaped: a raw `{@html}` must be justified with an inline disable, not slipped in.
		}
	},
	{ ignores: ['.svelte-kit/', 'build/', 'dist/', 'node_modules/', 'src/lib/tokens.ts', 'static/'] }
);
