<!--
@component
The console: one route, one surface.

The path is the whole of the page's input. The v2 shell turns it into the core,
a full screen or the car's display; a navigation inside the console changes the
path without remounting, which keeps the socket, the microphone and a turn in
flight alive across it.
-->
<script lang="ts">
	import { page } from '$app/state';
	import Shell from '$lib/ds/shell/Shell.svelte';
</script>

<!-- No <svelte:head> title: the shell names the tab after the screen and the
     state, and a head title here would fight it on every navigation. -->
<Shell path={page.url.pathname} session={page.url.searchParams.get('session') ?? ''} />
