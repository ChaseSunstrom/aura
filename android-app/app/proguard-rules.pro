# Jarvis app ProGuard/R8 rules.
#
# Minification is OFF in both build types right now (see app/build.gradle.kts),
# so nothing here is load-bearing yet. These rules are what you need the moment
# you flip isMinifyEnabled = true.

# Components are named as strings in AndroidManifest.xml — R8 keeps
# manifest-referenced classes automatically, but the automation module's
# services are instantiated by the system, so keep their members too.
-keep class ai.aura.app.**.*Service { *; }
-keep class ai.aura.app.**.*Receiver { *; }
-keep class ai.aura.app.**.*Activity { *; }

# ApprovalBridge is the cross-module consent entry point; other modules may
# reach it reflectively during wiring.
-keep class ai.aura.app.ui.ApprovalBridge { *; }

# OkHttp / Okio ship their own consumer rules; these silence the known
# optional-dependency warnings.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# Kotlin coroutines internals referenced only on other platforms.
-dontwarn kotlinx.coroutines.**
