// `panel.chat` from the Hermes transcript.
//
// The reducer's `ChatMessage` is the shape the socket produces and the old
// `ChatMessage.svelte` drew; the body draws `ChatTurn`, which is what a turn
// looks like to a person: who said it, the words, whether it is still arriving.
// The tool calls a message carries are NOT folded in here — they are the
// timeline panel's rows (`timeline.ts`), and drawing them twice was the old
// voice screen's "tool calls inline and in the strip" duplication.

import type { ChatMessage } from '$lib/chat';
import { shapeReply } from '$lib/hermes/hud';
import type { ChatTurn } from '$lib/glass';
import { clockOf } from './time';

export function chatTurns(messages: readonly ChatMessage[]): ChatTurn[] {
	return messages.map((m) => {
		const turn: ChatTurn = {
			id: m.id,
			role: m.role === 'user' ? 'you' : 'jarvis',
			// The reply is shaped for the eye (blank-line runs collapsed); a
			// person's own words are kept as typed.
			text: m.role === 'user' ? m.content : shapeReply(m.content),
			at: clockOf(m.at)
		};
		// The persona that spoke it, kept on the message so the label outlives a switch.
		if (m.role !== 'user' && m.persona && m.persona !== 'jarvis') turn.who = m.persona;
		if (m.thinking) turn.reasoning = m.thinking;
		if (m.pending) turn.streaming = true;
		return turn;
	});
}
