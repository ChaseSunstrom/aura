package ai.aura.app.car.video

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.Test

/**
 * The platform ends the screen-sharing grant whenever the phone locks, and the car said
 * nothing: the music stopped and the owner had no way to know why. The notice is said
 * once per grant lost while the car is connected, never for a grant that was not held,
 * and never after the car went first.
 */
class ProjectionLossNoticeTest {

    private val said = mutableListOf<String>()
    private val notice = ProjectionLossNotice { said += it }

    @Test
    fun a_grant_stopped_while_the_car_is_connected_is_said_once_and_names_the_fix() {
        notice.carConnected(true)
        notice.granted()
        notice.stopped()
        assertEquals(1, said.size)
        assertTrue(said[0].contains("tap", ignoreCase = true), said[0])
        assertTrue(said[0].contains("lock", ignoreCase = true), said[0])
    }

    @Test
    fun the_several_paths_that_report_one_loss_say_it_once() {
        notice.carConnected(true)
        notice.granted()
        notice.stopped()
        notice.stopped()
        notice.stopped()
        assertEquals(1, said.size)
    }

    @Test
    fun nothing_is_said_for_a_grant_that_was_never_given() {
        notice.carConnected(true)
        notice.stopped()
        assertTrue(said.isEmpty())
    }

    @Test
    fun nothing_is_said_when_the_car_went_first() {
        notice.carConnected(true)
        notice.granted()
        notice.carConnected(false)
        notice.stopped()
        assertTrue(said.isEmpty())
    }

    @Test
    fun a_grant_held_from_an_earlier_drive_is_not_announced_to_a_car_that_never_saw_it_given() {
        notice.granted()
        notice.carConnected(true)
        notice.stopped()
        // Held, then the car appeared: the loss is real to this drive.
        assertEquals(1, said.size)
    }

    @Test
    fun a_second_grant_lost_is_said_again() {
        notice.carConnected(true)
        notice.granted()
        notice.stopped()
        notice.granted()
        notice.stopped()
        assertEquals(2, said.size)
    }

    @Test
    fun a_failing_voice_does_not_stop_the_loss_being_recorded() {
        val boom = ProjectionLossNotice { error("no voice") }
        boom.carConnected(true)
        boom.granted()
        boom.stopped()
        boom.stopped()
        // Said (and failed) once; the second report is the same loss.
        assertEquals(false, boom.held)
    }
}
