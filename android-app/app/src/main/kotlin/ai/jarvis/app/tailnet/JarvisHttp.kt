package ai.jarvis.app.tailnet

import ai.jarvis.app.tailnet.JarvisHttp
import okhttp3.OkHttpClient

/**
 * Every OkHttp client the app builds for Jarvis starts here, so a request to a
 * tailnet host goes through the embedded tailnet link when it is on
 * ([TailnetLink]) and resolves as it always did when it is off.
 */
object JarvisHttp {
    fun builder(): OkHttpClient.Builder =
        OkHttpClient.Builder().dns(TailnetLink.dns).socketFactory(TailnetLink.socketFactory)
}
