// Root build file. Plugins are declared here and applied in :app.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.protobuf) apply false
}

/*
 * No Google Play Services anywhere in the merged graph, transitively (Headway's
 * rule, inherited with its stack -- ADR-0047). `./gradlew checkNoGms` fails if
 * any resolvable configuration of any module pulls in a GMS, Firebase, ML Kit
 * or Play artifact.
 */
val forbiddenDependencyGroups = listOf(
    "com.google.android.gms",
    "com.google.firebase",
    "com.google.mlkit",
    "com.google.android.play",
)

subprojects {
    tasks.register("checkNoGms") {
        group = "verification"
        description = "Fails if any resolvable configuration pulls in Google Play Services."
        val projectPath = path
        val configurationsToCheck = provider {
            configurations
                .filter { it.isCanBeResolved }
                .filterNot { it.name.contains("lintClassPath", ignoreCase = true) }
        }
        doLast {
            val offenders = mutableListOf<String>()
            configurationsToCheck.get().forEach { config ->
                runCatching {
                    config.incoming.resolutionResult.allComponents.forEach { component ->
                        val id = component.id.displayName
                        if (forbiddenDependencyGroups.any { id.startsWith(it) }) {
                            offenders += "$projectPath / ${config.name} -> $id"
                        }
                    }
                }
            }
            if (offenders.isNotEmpty()) {
                throw GradleException(
                    "Google Play Services dependencies detected:\n" +
                        offenders.distinct().sorted().joinToString("\n") { "  $it" },
                )
            }
        }
    }
}

tasks.register("checkNoGms") {
    group = "verification"
    description = "Aggregate GMS check across all modules."
    dependsOn(subprojects.map { "${it.path}:checkNoGms" })
}
