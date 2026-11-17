// Standalone Jarvis Android app. No Home Assistant, no Google Play Services.
pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    // Modules may not declare their own repositories; everything resolves here.
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "jarvis-android"
include(":app")
// The frame-timing macrobenchmark (GLASS_HUD §8.3 rule 6): a com.android.test
// module that drives the installed app on a device or emulator. Nothing under it
// runs in a JVM test or in assembleDebug; it exists so the CI job on an
// `android-device` runner has something to run the day one exists.
include(":macrobenchmark")

// Headway's car stack (ADR-0047), imported with its history under car/. The
// JVM-only modules carry the protocol and the emulator-backed acceptance suite
// and must stay free of Android; the three Android libraries capture video,
// inject input and move audio. Headway's app became ai.jarvis.app.car.
include(":car-protocol")
project(":car-protocol").projectDir = file("car/core-protocol")
include(":car-transport")
project(":car-transport").projectDir = file("car/core-transport")
include(":car-dash")
project(":car-dash").projectDir = file("car/core-dash")
include(":car-voice")
project(":car-voice").projectDir = file("car/core-voice")
include(":car-emulator")
project(":car-emulator").projectDir = file("car/headunit-emulator")
include(":car-video")
project(":car-video").projectDir = file("car/core-video")
include(":car-input")
project(":car-input").projectDir = file("car/core-input")
include(":car-audio")
project(":car-audio").projectDir = file("car/core-audio")
