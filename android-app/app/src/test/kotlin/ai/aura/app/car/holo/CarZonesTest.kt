package ai.aura.app.car.holo

import ai.aura.app.ui.theme.JarvisTokens
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The drive's fixed zones (T-0613, Car.dc.html): next turn top left, the trip top right,
 * what Jarvis says bottom left, the core bottom right and the one card beside it.
 *
 * A zone that slides under another on some head unit is text the driver cannot read, so
 * every size the goldens record is checked for overlap and for staying inside the area the
 * host leaves visible; the targets are checked against the car's 52 dp, which does not
 * shrink with the rest.
 */
class CarZonesTest {

    private val sizes = listOf(
        Triple(800f, 480f, 1f),
        Triple(1280f, 720f, 1.5f),
        Triple(1920f, 720f, 1.5f),
    )

    @Test
    fun `the zones fit the whole surface at every recorded size without overlapping`() {
        for ((w, h, d) in sizes) {
            val visible = Box(0f, 0f, w, h)
            check("${w}x$h", CarZones.layout(visible, d), visible, d)
        }
    }

    @Test
    fun `the zones stay inside a visible area the host has cut down`() {
        for ((w, h, d) in sizes) {
            // A rail down the left and a bar along the bottom, as Android Auto draws them.
            val visible = Box(96f * d, 0f, w, h - 40f * d)
            check("${w}x$h cut", CarZones.layout(visible, d), visible, d)
        }
    }

    @Test
    fun `the core docks bottom right, where AURA's galaxy and JARVIS's reactor both sit`() {
        // T-0871: the galaxy takes the reactor's corner; the corner itself must not move.
        for ((w, h, d) in sizes) {
            val visible = Box(0f, 0f, w, h)
            val z = CarZones.layout(visible, d)
            val c = z.core
            assertTrue("${w}x$h: core $c is in the bottom-right quarter", c.left >= w / 2 && c.top >= h / 2)
            for ((n, b) in listOf("turn" to z.turn, "trip" to z.trip, "subtitle" to z.subtitle, "card" to z.card)) {
                assertTrue("${w}x$h: $n $b is neither left of nor above the core $c", b.right <= c.left + EPS || b.bottom <= c.top + EPS)
            }
        }
    }

    @Test
    fun `with no room beside the card the subtitle goes below it instead of behind it`() {
        val d = 1f
        val visible = Box(220f, 0f, 800f, 480f)
        val z = CarZones.layout(visible, d)
        check("narrow", z, visible, d)
        assertTrue("subtitle ${z.subtitle} sits below card ${z.card}", z.subtitle.top >= z.card.bottom)
    }

    @Test
    fun `the targets keep the car's size when everything else shrinks`() {
        val z = CarZones.layout(Box(0f, 0f, 800f, 480f), 1f)
        assertTrue("a 480 dp screen scales the zones down", z.scale < 1f)
        for (b in z.buttons(3)) assertTrue("$b", b.height >= JarvisTokens.Size.CAR_TOUCH - EPS)
    }

    private fun check(name: String, z: CarZones, visible: Box, density: Float) {
        val zones = listOf("turn" to z.turn, "trip" to z.trip, "subtitle" to z.subtitle, "card" to z.card, "core" to z.core)
        for ((n, b) in zones) {
            assertTrue("$name: $n $b has no area", b.width > 0f && b.height > 0f)
            assertTrue("$name: $n $b leaves the visible $visible", visible.contains(b))
        }
        for (i in zones.indices) for (j in i + 1 until zones.size) {
            val (an, a) = zones[i]
            val (bn, b) = zones[j]
            assertFalse("$name: $an $a overlaps $bn $b", a.intersects(b))
        }
        val touch = JarvisTokens.Size.CAR_TOUCH * density - EPS
        for (count in 1..4) {
            val buttons = z.buttons(count)
            for (b in buttons) {
                assertTrue("$name: $count buttons, $b under 52 dp", b.width >= touch && b.height >= touch)
                assertTrue("$name: $count buttons, $b outside the card ${z.card}", z.card.contains(b))
            }
            for (i in buttons.indices) for (j in i + 1 until buttons.size) {
                assertFalse("$name: buttons overlap", buttons[i].intersects(buttons[j]))
            }
        }
    }

    @Test
    fun `one card at a time and the most urgent one wins the slot`() {
        fun card(kind: CarCard.Kind, title: String = kind.name) = CarCard(kind, "", title)
        val music = card(CarCard.Kind.MUSIC)
        val message = card(CarCard.Kind.MESSAGE)
        val call = card(CarCard.Kind.CALL)
        val approval = card(CarCard.Kind.APPROVAL)
        assertEquals(call, pickCard(listOf(music, message, approval, call)))
        assertEquals(approval, pickCard(listOf(music, approval, message)))
        // Two of a kind: the first one raised keeps the slot; nothing showing is no card.
        val second = card(CarCard.Kind.MESSAGE, "second")
        assertEquals(message, pickCard(listOf(music, message, second)))
        assertNull(pickCard(emptyList()))
    }

    private companion object {
        const val EPS = 1e-3f
    }
}
