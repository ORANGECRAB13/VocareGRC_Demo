pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "VocaNative"
// :session is owned by android-session; it is included from the start so that
// owner never has to touch this file (native/CONTRACT.md §1).
include(":app", ":core", ":session")
