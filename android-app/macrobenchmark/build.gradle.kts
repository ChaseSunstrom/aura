// The frame-timing macrobenchmark for the Glass HUD (GLASS_HUD §8.3 rule 6:
// p95 frame ≤ 8.3 ms at 120 Hz on the Pixel, ≤ 16.7 ms elsewhere, no frame
// over 50 ms) on the 8-panel scene.
//
// A `com.android.test` module: it builds no app of its own, it installs and
// drives `:app`'s `benchmark` variant (release, debug-signed, profileable — see
// app/build.gradle.kts) through UiAutomator and reads the frame timings the
// platform reports. That needs a device or an emulator with a GPU; there is
// none on any CI runner today, which is why `android-macrobench`
// (.gitlab/ci/quality-hud.yml) only runs on a runner tagged `android-device`
// and the V2_TASKS row is BLOCKED. Nothing here is compiled by assembleDebug or
// by the JVM unit tests: `beforeVariants` keeps only the benchmark variant, so
// a `gradle :app:...` invocation pays configuration for this module and no more.
//
// Plugin ids resolve from the root classpath (AGP and Kotlin are declared in
// build.gradle.kts with `apply false`); versions below are pinned strings for
// the reason app/build.gradle.kts gives for its instrumentation block.
plugins {
    id("com.android.test")
    id("org.jetbrains.kotlin.android")
}

val benchmarkMacro = "1.3.3"
val androidxTestExtJunit = "1.2.1"
val uiAutomator = "2.3.0"

android {
    namespace = "ai.aura.app.macrobenchmark"
    compileSdk = 35

    defaultConfig {
        // Macrobenchmark's floor is 23; the app's is 29 and the benchmark
        // inherits it, so a device the app cannot run on is refused up front.
        minSdk = 29
        targetSdk = 35
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // Not a device-specific suppression: an emulator is allowed, and its
        // numbers are labelled by the harness as emulator numbers.
        testInstrumentationRunnerArguments["androidx.benchmark.suppressErrors"] = "EMULATOR,LOW-BATTERY"
    }

    buildTypes {
        // Matches the app's `benchmark` build type; the fallback keeps Gradle
        // from failing to resolve when a library dependency has only release.
        create("benchmark") {
            isDebuggable = true
            signingConfig = getByName("debug").signingConfig
            matchingFallbacks += listOf("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    targetProjectPath = ":app"
    experimentalProperties["android.experimental.self-instrumenting"] = true

    sourceSets.getByName("main").java.srcDir("src/main/kotlin")
}

androidComponents {
    beforeVariants(selector().all()) { variant ->
        variant.enable = variant.buildType == "benchmark"
    }
}

dependencies {
    implementation("androidx.benchmark:benchmark-macro-junit4:$benchmarkMacro")
    implementation("androidx.test.ext:junit:$androidxTestExtJunit")
    implementation("androidx.test.uiautomator:uiautomator:$uiAutomator")
}
