package ai.aura.app.car.phone

import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.drawable.Icon
import android.os.Process
import android.service.notification.StatusBarNotification
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * What the car's call card is told about a call, read from the dialer's notification (T-0760):
 * the caller's picture and when the call began. No call is placed or answered here; the
 * notifications are built by hand.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarPhoneTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()
    private val tap: PendingIntent = PendingIntent.getBroadcast(ctx, 0, Intent("x"), PendingIntent.FLAG_IMMUTABLE)

    /** An old-style dialer's call notification: plain actions, no CallStyle (the path the target phone takes). */
    private fun call(vararg actions: String, build: Notification.Builder.() -> Unit = {}): StatusBarNotification {
        val n = Notification.Builder(ctx, "c").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Sam Example").setCategory(Notification.CATEGORY_CALL).setOngoing(true)
            .apply { actions.forEach { addAction(Notification.Action.Builder(null, it, tap).build()) } }
            .apply(build).build()
        return StatusBarNotification("com.example.dialer", "com.example.dialer", 1, null, 0, 0, 0, n, Process.myUserHandle(), 1L)
    }

    @After
    fun clear() = CarPhone.clear()

    @Test
    fun `the caller's picture is a small JPEG data URL, and none is the empty string`() {
        val icon = Icon.createWithBitmap(Bitmap.createBitmap(300, 300, Bitmap.Config.ARGB_8888))
        val withPhoto = CarPhone.readLiveCall(ctx, call("Hang up") { setLargeIcon(icon) })
        assertTrue(withPhoto!!.photo, withPhoto.photo.startsWith("data:image/jpeg;base64,"))
        CarPhone.clear() // the next call: a picture is kept for the call it was drawn for
        assertEquals("", CarPhone.readLiveCall(ctx, call("Hang up") { setContentTitle("Someone Else") })!!.photo)
    }

    // The path a phone with a modern dialer takes: the caller is a Person with their own icon.
    @Test
    fun `a CallStyle call gives the person's picture and its chronometer's start`() {
        val person = android.app.Person.Builder().setName("Sam Example")
            .setIcon(Icon.createWithBitmap(Bitmap.createBitmap(64, 64, Bitmap.Config.ARGB_8888))).build()
        val n = Notification.Builder(ctx, "c").setSmallIcon(android.R.drawable.ic_media_play)
            .setCategory(Notification.CATEGORY_CALL).setOngoing(true)
            .setStyle(Notification.CallStyle.forOngoingCall(person, tap))
            .setWhen(7_000).setUsesChronometer(true).build()
        val read = CarPhone.readLiveCall(ctx, StatusBarNotification("com.example.dialer", "com.example.dialer", 1, null, 0, 0, 0, n, Process.myUserHandle(), 1L))!!
        assertEquals("Sam Example", read.who)
        assertTrue(read.photo, read.photo.startsWith("data:image/jpeg;base64,"))
        assertEquals(7_000L, read.since)
    }

    @Test
    fun `a ringing call has not begun, and an answered one began when its chronometer says`() {
        assertNull(CarPhone.readLiveCall(ctx, call("Answer", "Decline") { setWhen(5_000).setUsesChronometer(true) })!!.since)
        assertEquals(5_000L, CarPhone.readLiveCall(ctx, call("Hang up") { setWhen(5_000).setUsesChronometer(true) })!!.since)
        // A plain `when` is when the notification was posted, not when the call was answered.
        assertNull(CarPhone.readLiveCall(ctx, call("Hang up") { setWhen(5_000) })!!.since)
    }

    @Test
    fun `a dialer with no chronometer began when the call was first seen answered, and keeps that`() {
        assertTrue(CarPhone.offer(ctx, call("Answer", "Decline")))
        assertNull(CarPhone.call!!.since)
        assertTrue(CarPhone.offer(ctx, call("Hang up")))
        val began = CarPhone.call!!.since
        assertNotNull(began)
        Thread.sleep(5)
        assertTrue(CarPhone.offer(ctx, call("Hang up")))
        assertEquals(began, CarPhone.call!!.since)
    }
}
