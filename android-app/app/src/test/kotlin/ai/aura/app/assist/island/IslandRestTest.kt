package ai.aura.app.assist.island

import ai.aura.app.assist.island.IslandRest.Box
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The resting island's place and when it shows (T-0890): the owner, 2026-11-09, "that should be
 * where the dynamic island look is anyways, as it is slightly below it". Pure: the window's
 * offsets from the cutout's box. Does not cover what the window manager does with them on a
 * given phone (IslandRestHostTest drives the real params; the phone itself is the owner's).
 */
class IslandRestTest {
    @Test
    fun `the conversation's island sits on a centred hole, with room for the hole across and below it`() {
        // Pixel-shaped (T-0968): the hole's outline on a 1080 px screen, a 154 px pill (44 dp at 3.5).
        val a = IslandRest.anchor(1080, Box(495, 30, 585, 120), height = 154, slack = 28)!!
        assertEquals(IslandRest.At(0, 0), a.at)
        assertEquals(90, a.notch)
        assertEquals(120, a.clear)
        // A hole lower than half the pill: centred on it, and the rows start below it.
        val low = IslandRest.anchor(1080, Box(510, 100, 570, 160), height = 100, slack = 28)!!
        assertEquals(IslandRest.At(0, 80), low.at)
        assertEquals(80, low.clear)
    }

    @Test
    fun `no hole, or one in a corner, leaves the conversation's island under the status bar`() {
        assertEquals(null, IslandRest.anchor(1080, null, height = 154, slack = 28))
        assertEquals(null, IslandRest.anchor(1080, Box(60, 30, 150, 120), height = 154, slack = 28))
    }

    @Test
    fun `it is centred on a centred camera hole, not under the status bar`() {
        // A 1080 px screen, a 40 px hole from 30 to 70, an 80 px status bar, a 64 px island.
        val at = IslandRest.at(screenWidth = 1080, cutout = Box(520, 30, 560, 70), statusBar = 80, height = 64)
        assertEquals(0, at.x)
        assertEquals(50 - 32, at.y)
    }

    @Test
    fun `it follows a hole off the centre`() {
        val at = IslandRest.at(screenWidth = 1080, cutout = Box(60, 20, 100, 60), statusBar = 80, height = 64)
        assertEquals(80 - 540, at.x)
        assertEquals(40 - 32, at.y)
    }

    @Test
    fun `with no cutout it sits in the middle of the status bar, never above the screen`() {
        assertEquals(IslandRest.At(0, 8), IslandRest.at(screenWidth = 1080, cutout = null, statusBar = 80, height = 64))
        assertEquals(IslandRest.At(0, 0), IslandRest.at(screenWidth = 1080, cutout = Box(530, 0, 550, 10), statusBar = 0, height = 64))
    }

    @Test
    fun `it is never smaller than the hole it sits on`() {
        assertEquals("a small hole: its resting height", 64, IslandRest.height(64, Box(0, 0, 40, 40), 12, statusBar = 0))
        assertEquals("a big hole and a margin round it", 124, IslandRest.height(64, Box(0, 10, 100, 110), 12, statusBar = 0))
        assertEquals("no hole", 64, IslandRest.height(64, null, 12, statusBar = 0))
    }

    @Test
    fun `it sizes on the hole itself, not the status bar's rectangle round it`() {
        // A Pixel's bounding rect runs from the screen's top edge to below the hole (the status
        // bar's approximation); the cutout's own outline is the hole. The owner, 2026-11-10, on a
        // Pixel 10 Pro XL: "way taller than it should be and goes off the screen".
        val outline = Box(495, 30, 585, 120)
        val rect = Box(480, 0, 600, 150)
        assertEquals(outline, IslandRest.hole(outline, rect))
        assertEquals("no outline: the rect", rect, IslandRest.hole(null, rect))
        assertEquals("an empty outline: the rect", rect, IslandRest.hole(Box(0, 0, 0, 0), rect))
        assertEquals("cutouts on two sides, one outline: the top rect", rect, IslandRest.hole(Box(495, 30, 585, 2380), rect))
        assertEquals("no top rect: the hole is at the side (landscape), none at the top", null, IslandRest.hole(Box(0, 495, 90, 585), null))
        val tall = IslandRest.height(84, outline, 16, statusBar = 161)
        assertEquals("the hole and a margin round it", 122, tall)
        assertEquals("centred on the hole, inside the screen", IslandRest.At(0, 14), IslandRest.at(1080, outline, 161, tall))
    }

    @Test
    fun `it is never taller than the status bar`() {
        assertEquals(80, IslandRest.height(64, Box(0, 0, 100, 100), 12, statusBar = 80))
        assertEquals("no status bar to fit: uncapped", 124, IslandRest.height(64, Box(0, 0, 100, 100), 12, statusBar = 0))
    }

    @Test
    fun `it shows with the setting on, nothing else up, the status bar showing and the screen on`() {
        assertTrue(IslandRest.shown(setting = true, conversation = false, statusBar = true, screenOn = true))
        assertFalse("turned off", IslandRest.shown(setting = false, conversation = false, statusBar = true, screenOn = true))
        assertFalse("the conversation's island has the top", IslandRest.shown(setting = true, conversation = true, statusBar = true, screenOn = true))
        assertFalse("another app is full screen", IslandRest.shown(setting = true, conversation = false, statusBar = false, screenOn = true))
        assertFalse("nobody is looking", IslandRest.shown(setting = true, conversation = false, statusBar = true, screenOn = false))
        assertTrue(
            "the app's own screen hid the bar: the island stands in for it",
            IslandRest.shown(setting = true, conversation = false, statusBar = false, screenOn = true, ownScreen = true),
        )
    }
}
