// NewAl Code Lite: NewAl Code on Android phones with 2-4 GB of RAM (see README.md). Its own Gradle project, so it
// builds apart from the H33 app at the top of this repository.
pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories { google(); mavenCentral() }
}
rootProject.name = "NewAlCodeLite"
include(":app")
