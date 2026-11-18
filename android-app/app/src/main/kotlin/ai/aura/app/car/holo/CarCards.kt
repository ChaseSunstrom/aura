package ai.aura.app.car.holo

import ai.aura.app.car.holo.CarCard.Kind
import java.util.EnumMap

/**
 * Who has a card to show, and which of them has the one slot (T-0648).
 *
 * Each source (a call, a message, the next event, what is playing, an arrival, an approval)
 * [set]s its own card while it is live and [clear]s it when it ends; [pickCard] decides
 * who is on screen. [onChange] hears only a change in what is on screen, so a quiet card
 * arriving behind a call, or a source that re-reads on a timer, spends no template refresh.
 *
 * A source that must know when its card is actually in front of the driver (the approval, whose
 * countdown must not run behind a call: T-0663) passes onShown, which hears true when that entry
 * takes the slot and false when it loses it, to another kind, to a newer card of its own kind,
 * or to a clear.
 *
 * Main thread only: sources call in from their listeners, which the screen posts there.
 * Does not cover a source that forgets to clear: [CarNavScreen] clears all on stop, and the
 * time-based cards are re-read on its tick.
 */
class CarCards(private val onChange: (CarCard?, (CarAction) -> Unit) -> Unit) {

    private class Entry(
        val card: CarCard,
        val onAction: (CarAction) -> Unit,
        val onShown: (Boolean) -> Unit,
    )

    private val live = EnumMap<Kind, Entry>(Kind::class.java)
    private var shown: Entry? = null

    /** The card on screen. */
    val current: CarCard? get() = shown?.card

    /** onShown comes first so a trailing lambda stays the card's [CarAction] handler. */
    fun set(card: CarCard, onShown: (Boolean) -> Unit = {}, onAction: (CarAction) -> Unit = {}) {
        live[card.kind] = Entry(card, onAction, onShown)
        update()
    }

    fun clear(kind: Kind) {
        if (live.remove(kind) != null) update()
    }

    fun clearAll() {
        live.clear()
        update()
    }

    /** A tap or a phrase for the card on screen; it goes to that card's source only. */
    fun act(action: CarAction) {
        shown?.onAction?.invoke(action)
    }

    private fun update() {
        val pick = pickCard(live.values.map { it.card })
        val next = pick?.let { live.getValue(it.kind) }
        val prev = shown
        // [act] reads [shown], so a new owner of the same words is current without a push.
        shown = next
        if (next !== prev) {
            prev?.onShown?.invoke(false)
            next?.onShown?.invoke(true)
        }
        if (next?.card == prev?.card) return
        onChange(next?.card, next?.onAction ?: {})
    }
}
