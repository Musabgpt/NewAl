#!/usr/bin/env bash
# Makes what NewAl Code Lite's APK carries besides its Java code:
#   app/src/main/jniLibs/<abi>/  libnewalpy.so (python), libpython3.14.so and its libraries, libllama-server.so
#                                (+ libllama-server-dotprod.so on arm64, for CPUs with dot-product instructions)
#   app/build/generated/newal-assets/  python-stdlib.zip, python-dynload-<abi>.zip, newal_code.zip, cacert.pem
#
#   ANDROID_NDK=/path/to/ndk native/prepare.sh [abi ...]      (default: arm64-v8a x86_64)
# Python is python.org's official Android build; llama.cpp is built from its newest release (LLAMA_TAG to pin).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
NDK=${ANDROID_NDK:?set ANDROID_NDK to the Android NDK folder}
PY=${PY_VERSION:-3.14.4}
PYSHORT=3.14
API=26
ABIS=${*:-arm64-v8a x86_64}
WORK=${WORK:-$ROOT/build/native}
ASSETS=$ROOT/app/build/generated/newal-assets
JNI=$ROOT/app/src/main/jniLibs
TOOLS=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
mkdir -p "$WORK" "$ASSETS" "$JNI"

triplet() { case "$1" in arm64-v8a) echo aarch64-linux-android ;; x86_64) echo x86_64-linux-android ;;
                         *) echo "unknown ABI $1" >&2; exit 1 ;; esac; }

# ---------------------------------------------------------------------------------------------------- Python
for abi in $ABIS; do
  t=$(triplet "$abi")
  tgz="$WORK/python-$PY-$t.tar.gz"
  [ -f "$tgz" ] || curl -fsSL -o "$tgz" "https://www.python.org/ftp/python/$PY/python-$PY-$t.tar.gz"
  rm -rf "$WORK/py-$abi" && mkdir -p "$WORK/py-$abi" && tar -xzf "$tgz" -C "$WORK/py-$abi"
  pre="$WORK/py-$abi/prefix"
  mkdir -p "$JNI/$abi"
  for lib in libpython$PYSHORT.so libssl_python.so libcrypto_python.so libsqlite3_python.so; do
    cp "$pre/lib/$lib" "$JNI/$abi/"
  done
  "$TOOLS/$t$API-clang" -O2 -o "$JNI/$abi/libnewalpy.so" "$ROOT/native/launcher.c" \
    -I "$pre/include/python$PYSHORT" -L "$pre/lib" -lpython$PYSHORT -Wl,-rpath,'$ORIGIN'
  (cd "$pre/lib/python$PYSHORT/lib-dynload" && rm -f "$ASSETS/python-dynload-$abi.zip" &&
   zip -q -r "$ASSETS/python-dynload-$abi.zip" . -x '_test*' '_ctypes_test*' 'xxlimited*' 'xxsubtype*' '_xxtestfuzz*')
done

# The standard library (one copy for every ABI; each ABI's sysconfig data added), without tests and GUI parts.
first=$(echo $ABIS | cut -d' ' -f1)
rm -f "$ASSETS/python-stdlib.zip"
(cd "$WORK/py-$first/prefix" && zip -q -r "$ASSETS/python-stdlib.zip" "lib/python$PYSHORT" \
   -x "lib/python$PYSHORT/lib-dynload/*" "lib/python$PYSHORT/test/*" "lib/python$PYSHORT/idlelib/*" \
      "lib/python$PYSHORT/tkinter/*" "lib/python$PYSHORT/turtledemo/*" "lib/python$PYSHORT/ensurepip/*" \
      "lib/python$PYSHORT/config-$PYSHORT-*" "*/__pycache__/*")
for abi in $ABIS; do
  (cd "$WORK/py-$abi/prefix" && zip -q "$ASSETS/python-stdlib.zip" lib/python$PYSHORT/_sysconfig*)
done

# NewAl Code itself, and the certificates HTTPS needs (model downloads, APIs).
rm -f "$ASSETS/newal_code.zip"
(cd "$ROOT/../desktop" && zip -q -r "$ASSETS/newal_code.zip" newal_code -x '*/__pycache__/*')
cp "${CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}" "$ASSETS/cacert.pem"

# ---------------------------------------------------------------------------------------------------- llama.cpp
src="$WORK/llama.cpp"
if [ ! -d "$src" ]; then
  tag=${LLAMA_TAG:-$(git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp 'b*' |
                     sed 's|.*refs/tags/||' | sort -V | tail -1)}
  echo "llama.cpp $tag"
  git clone -q --depth 1 --branch "$tag" https://github.com/ggml-org/llama.cpp "$src"
fi
build() {   # abi, output name, extra cmake flags...
  local abi=$1 out=$2; shift 2
  local b="$WORK/llama-$out-$abi"
  cmake -S "$src" -B "$b" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI="$abi" \
    -DANDROID_PLATFORM=android-$API -DANDROID_STL=c++_static -DBUILD_SHARED_LIBS=OFF \
    -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DGGML_BACKEND_DL=OFF -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_SERVER=ON "$@" >/dev/null
  cmake --build "$b" --target llama-server -j "$(nproc)" >/dev/null
  "$TOOLS/llvm-strip" -o "$JNI/$abi/$out.so" "$b/bin/llama-server"
  echo "$abi/$out.so: $(du -h "$JNI/$abi/$out.so" | cut -f1)"
}
for abi in $ABIS; do
  case "$abi" in
    arm64-v8a)
      build "$abi" libllama-server -DGGML_CPU_ARM_ARCH=armv8-a                        # every 64-bit ARM phone
      build "$abi" libllama-server-dotprod -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16 ;;  # Cortex-A55/A75 and up
    x86_64)
      build "$abi" libllama-server -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON ;;  # emulators
  esac
done
ls -la "$ASSETS" "$JNI"/*
