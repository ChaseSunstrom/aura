package ai.jarvis.app.automation.actions.builtin

import android.telephony.SmsManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SmsSentTest {
    @Test fun `each failure code says why the text did not leave`() {
        assertTrue(SmsSent.reason(SmsManager.RESULT_ERROR_NO_SERVICE).contains("no mobile service"))
        assertTrue(SmsSent.reason(SmsManager.RESULT_ERROR_RADIO_OFF).contains("radio is off"))
        assertEquals("Android reported error 99", SmsSent.reason(99))
    }

    @Test fun `the wait ends inside the action's own timeout`() {
        assertTrue(SmsSent.WAIT_MS < SendSms.timeoutMs)
    }
}
