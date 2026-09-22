cmake_minimum_required(VERSION 3.22.1)
project(llama_bridge)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)

# Pin the inference engine so APK builds are reproducible. b11058 is a modern
# upstream Android-capable baseline with the current sampler/state APIs.
FetchContent_Declare(
    llama_cpp
    GIT_REPOSITORY https://github.com/ggml-org/llama.cpp.git
    GIT_TAG f072b103714dfa1eee531f80b24512faf38e3dd2
    GIT_SHALLOW FALSE
)

# Android ARM64 baseline: use the optimized CPU backend rather than the
# legacy generic CPU path. GPU backends stay disabled until device benchmarks
# justify enabling them.
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_SYSTEM_ARCH ARM CACHE STRING "" FORCE)
set(GGML_CPU_KLEIDIAI ON CACHE BOOL "" FORCE)
set(GGML_OPENMP ON CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN OFF CACHE BOOL "" FORCE)
set(GGML_OPENCL OFF CACHE BOOL "" FORCE)
set(GGML_CUDA OFF CACHE BOOL "" FORCE)
set(GGML_METAL OFF CACHE BOOL "" FORCE)

set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)

FetchContent_MakeAvailable(llama_cpp)

add_library(llama_bridge SHARED llama_bridge.cpp)

target_include_directories(llama_bridge PRIVATE
    ${llama_cpp_SOURCE_DIR}/include
    ${llama_cpp_SOURCE_DIR}/ggml/include
)

find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(llama_bridge
    llama
    llama-common
    ${log-lib}
    ${android-lib}
)
