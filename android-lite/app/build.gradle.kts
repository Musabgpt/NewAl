plugins {
    id("com.android.application")
}

// The native programs (python, llama-server) and the assets (Python's library, NewAl Code) are made by
// native/prepare.sh into src/main/jniLibs and build/generated/assets; see README.md.
android {
    namespace = "dev.newal.code.lite"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.newal.code.lite"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("NEWAL_BUILD") ?: "1").toInt()
        versionName = "0.1." + (System.getenv("NEWAL_BUILD") ?: "0")
    }

    sourceSets {
        getByName("main") {
            assets.srcDir(layout.buildDirectory.dir("generated/newal-assets"))
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Signed with the debug key so the APK installs directly (sideloading, like the other NewAl builds).
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    packaging {
        // The programs run from the app's native library folder: Android must unpack them at install.
        jniLibs { useLegacyPackaging = true }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
