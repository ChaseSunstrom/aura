package ai.aura.app.car.holo

import ai.aura.app.car.dash.tiles.CarMessage
import ai.aura.app.car.dash.tiles.ReplySlot
import ai.aura.app.car.holo.CarCard.Kind
import ai.aura.app.car.phone.LiveCall
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** What each source puts in the car's one card slot, and when it has nothing to put there (T-0648). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarSourcesTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()
    private val now = 1_000_000_000L

    private fun message(
        key: String = "k",
        text: String = "On my way",
        at: Long = now,
        sender: String = "Isa",
        reply: ReplySlot? = null,
    ) = CarMessage(key, "pkg", sender, text, at, reply)

    private fun slot() = ReplySlot(
        "Reply",
        PendingIntent.getBroadcast(ctx, 0, Intent("x").setPackage(ctx.packageName), PendingIntent.FLAG_IMMUTABLE),
        arrayOf(RemoteInput.Builder("r").build()),
        emptyList(),
    )

    // --- call ---------------------------------------------------------------------

    @Test
    fun `a ringing call offers answer and decline, a live one hang up`() {
        val ringing = CarSourceCards.call(ctx, LiveCall("Mum", true, null, null, "x"))
        assertEquals(Kind.CALL, ringing.kind)
        assertEquals("Incoming call", ringing.caption)
        assertEquals("Mum", ringing.title)
        assertEquals(listOf(CarSourceCards.ANSWER, CarSourceCards.DECLINE), ringing.actions.map { it.id })
        assertTrue(ringing.actions.first().primary)

        val live = CarSourceCards.call(ctx, LiveCall("Mum", false, null, null, "x"))
        assertEquals("On a call", live.caption)
        assertEquals(listOf(CarSourceCards.HANG_UP), live.actions.map { it.id })
    }

    // --- message ------------------------------------------------------------------

    @Test
    fun `the newest fresh message since the screen started gets the card`() {
        val feed = listOf(
            message("old", at = now - 5_000),
            message("new", text = "Running late", at = now - 1_000),
        )
        assertEquals("new", CarSourceCards.latestMessage(feed, now - 10_000, now, emptySet())?.key)
    }

    @Test
    fun `a message is skipped when stale, before the start, chatter, blank or put off`() {
        val since = now - 600_000
        val stale = message("a", at = now - CarSourceCards.MESSAGE_FRESH_MS - 1)
        val before = message("b", at = since - 1)
        val chatter = message("c", text = "Isa is typing...")
        val blank = message("d", text = "  ")
        val later = message("e")
        val feed = listOf(stale, before, chatter, blank, later)
        assertEquals("e", CarSourceCards.latestMessage(feed, since, now, emptySet())?.key)
        assertNull(CarSourceCards.latestMessage(feed, since, now, setOf("e")))
        assertNull(CarSourceCards.latestMessage(emptyList(), since, now, emptySet()))
    }

    @Test
    fun `the message card names the sender, reads on one line and offers reply only when the app does`() {
        val withReply = CarSourceCards.message(ctx, message(text = "Line one\nline [two]", reply = slot()))
        assertEquals(Kind.MESSAGE, withReply.kind)
        assertEquals("Message · Isa", withReply.caption)
        assertEquals("Line one line two", withReply.title)
        assertEquals(listOf(CarSourceCards.REPLY, CarSourceCards.LATER), withReply.actions.map { it.id })

        val without = CarSourceCards.message(ctx, message())
        assertEquals(listOf(CarSourceCards.LATER), without.actions.map { it.id })
    }

    @Test
    fun `a long message is cut to one glance`() {
        val title = CarSourceCards.message(ctx, message(text = "word ".repeat(100))).title
        assertTrue(title.length <= 140)
        assertTrue(title.endsWith("…"))
    }

    // --- calendar -----------------------------------------------------------------

    @Test
    fun `the next event is the soonest timed one within fifteen minutes`() {
        val min = 60_000L
        val events = listOf(
            CarEvent("Later", now + 20 * min, false),
            CarEvent("Birthday", now + 5 * min, true),
            CarEvent("Standup", now + 10 * min, false),
            CarEvent("Review", now + 12 * min, false),
        )
        assertEquals("Standup", CarSourceCards.nextEvent(events, now)?.title)
        assertNull(CarSourceCards.nextEvent(events.take(2), now))
        // Just begun still counts; begun long ago does not.
        assertEquals("Now", CarSourceCards.nextEvent(listOf(CarEvent("Now", now - 30_000, false)), now)?.title)
        assertNull(CarSourceCards.nextEvent(listOf(CarEvent("Over", now - 5 * min, false)), now))
        assertNull(CarSourceCards.nextEvent(listOf(CarEvent("", now + min, false)), now))
    }

    @Test
    fun `the calendar card says when, in minutes rounded up, or now`() {
        val soon = CarSourceCards.calendar(ctx, CarEvent("Standup", now + 9 * 60_000L + 1, false), "09:30", now)
        assertEquals(Kind.CALENDAR, soon.kind)
        assertEquals("Coming up", soon.caption)
        assertEquals("Standup", soon.title)
        assertEquals("At 09:30 · in 10 min", soon.detail)
        assertTrue(soon.actions.isEmpty())

        val started = CarSourceCards.calendar(ctx, CarEvent("Standup", now - 10_000, false), "09:30", now)
        assertEquals("At 09:30 · now", started.detail)
    }

    // --- music --------------------------------------------------------------------

    @Test
    fun `the music card has the track and previous, pause and next`() {
        val card = CarSourceCards.music(ctx, "Song", "Band")
        assertEquals(Kind.MUSIC, card.kind)
        assertEquals("Playing", card.caption)
        assertEquals("Song", card.title)
        assertEquals("Band", card.detail)
        assertEquals(
            listOf(CarSourceCards.PREVIOUS, CarSourceCards.PAUSE, CarSourceCards.NEXT),
            card.actions.map { it.id },
        )
        assertEquals(1, card.actions.count { it.primary })
    }

    // --- home ---------------------------------------------------------------------

    @Test
    fun `home is a destination called home that is close`() {
        assertTrue(CarSourceCards.isHome("Home", 400f))
        assertTrue(CarSourceCards.isHome("  home ", 999f))
        assertFalse(CarSourceCards.isHome("Home", 1_000f))
        assertFalse(CarSourceCards.isHome("Work", 100f))
        assertFalse(CarSourceCards.isHome("", 100f))
    }

    @Test
    fun `the home card says how far is left`() {
        val card = CarSourceCards.home(ctx, "Home", "0.2 mi")
        assertEquals(Kind.HOME, card.kind)
        assertEquals("Almost home", card.caption)
        assertEquals("0.2 mi to go", card.detail)
    }

    // --- the ladder ---------------------------------------------------------------

    @Test
    fun `every source's card wins the slot in the brief's order`() {
        val cards = listOf(
            CarSourceCards.music(ctx, "Song", "Band"),
            CarSourceCards.calendar(ctx, CarEvent("A", now + 60_000, false), "09:30", now),
            CarSourceCards.home(ctx, "Home", "300 ft"),
            CarSourceCards.message(ctx, message()),
            CarSourceCards.call(ctx, LiveCall("Mum", true, null, null, "x")),
        )
        var rest = cards
        val order = ArrayList<Kind>()
        while (rest.isNotEmpty()) {
            val pick = pickCard(rest)!!
            order.add(pick.kind)
            rest = rest - pick
        }
        assertEquals(listOf(Kind.CALL, Kind.MESSAGE, Kind.HOME, Kind.CALENDAR, Kind.MUSIC), order)
    }
}
