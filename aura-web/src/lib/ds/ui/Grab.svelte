<!--
@component
The handle at the top of a menu that has risen on a phone: the part a finger takes to drag
it down (`ui/drag.ts`). Drawn only where menus rise; a wide window's drawer has none.

Does not cover: the drag itself (the menu's own node carries `dragToClose`).
-->
<div class="grab" data-drag aria-hidden="true"><span></span></div>

<style>
	.grab {
		display: none;
	}
	@media (max-width: 720px) {
		.grab {
			flex: none;
			display: flex;
			align-items: center;
			justify-content: center;
			block-size: var(--jv-space-5);
			/* The drag is ours: without this the browser takes the move as a scroll. */
			touch-action: none;
		}
		span {
			inline-size: var(--jv-space-7);
			block-size: var(--jv-space-1);
			border-radius: var(--jv-radius-pill);
			background: var(--jv-text-faint);
			opacity: 0.6;
		}
	}
</style>
