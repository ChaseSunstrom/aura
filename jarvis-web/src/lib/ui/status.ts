/**
 * The four-plus-one states every screen implements (`$lib/ds/ui/State.svelte`
 * renders them). A plain module rather than the component's `<script module>`
 * because a bare `tsc --noEmit` only knows `*.svelte` as a default-export
 * ambient module: a named type export from the component made the type gate
 * report one error that svelte-check never saw.
 */
export type Status = 'loading' | 'ready' | 'empty' | 'error' | 'offline';
