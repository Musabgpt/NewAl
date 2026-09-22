plugins { id("com.android.application") }

android { namespace = "com.musab.aragpt2"; compileSdk = 36
    defaultConfig { applicationId = "com.musab.aragpt2"; minSdk = 28; targetSdk = 36; versionCode = 1; versionName = "1.0" }
    externalNativeBuild { cmake { path = file("src/main/cpp/CMakeLists.txt") } }
}

dependencies { implementation("androidx.appcompat:appcompat:1.7.0"); implementation("androidx.recyclerview:recyclerview:1.3.2") }
