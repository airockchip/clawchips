cmake_minimum_required(VERSION 3.18)
project(rkclaw_native_bridges LANGUAGES C CXX)

option(RKCLAW_BUILD_NATIVE_TESTS "Build RKClawServer native bridge smoke tests" OFF)

set(TOKENIZER_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/3rdparty/tokenizer" CACHE PATH
    "RKNN tokenizer package root")
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$")
    set(TOKENIZER_ARCH x86_64)
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
    set(TOKENIZER_ARCH aarch64)
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(arm|armv7|armv7l)$")
    set(TOKENIZER_ARCH armhf)
else()
    message(FATAL_ERROR "Unsupported tokenizer architecture: ${CMAKE_SYSTEM_PROCESSOR}")
endif()
set(TOKENIZER_LIBRARY
    "${TOKENIZER_ROOT}/lib/Linux/${TOKENIZER_ARCH}/libtokenizer.a"
    CACHE FILEPATH "RKNN tokenizer static library")

find_path(TOKENIZER_INCLUDE_DIR Tokenizer.h
    PATHS "${TOKENIZER_ROOT}/include" NO_DEFAULT_PATH)
if(NOT TOKENIZER_INCLUDE_DIR)
    message(FATAL_ERROR "Tokenizer.h was not found under ${TOKENIZER_ROOT}/include")
endif()
if(NOT EXISTS "${TOKENIZER_LIBRARY}")
    message(FATAL_ERROR "Tokenizer static library was not found: ${TOKENIZER_LIBRARY}")
endif()

set(XGRAMMAR_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/3rdparty/xgrammar" CACHE PATH
    "Pinned XGrammar source tree")
if(NOT EXISTS "${XGRAMMAR_ROOT}/CMakeLists.txt")
    message(FATAL_ERROR
        "XGrammar source was not found at ${XGRAMMAR_ROOT}; initialize native/3rdparty/xgrammar")
endif()
set(XGRAMMAR_BUILD_PYTHON_BINDINGS OFF CACHE BOOL "" FORCE)
set(XGRAMMAR_BUILD_CXX_TESTS OFF CACHE BOOL "" FORCE)
set(XGRAMMAR_ENABLE_CPPTRACE OFF CACHE BOOL "" FORCE)
# XGrammar's source-tree cmake/config.cmake enables Python bindings with a
# normal variable, which overrides the cache setting above. Its top-level
# CMakeLists checks the parent build directory first, so provide the native
# bridge's C++-only configuration there before adding the subdirectory.
file(WRITE "${CMAKE_BINARY_DIR}/config.cmake"
    "set(XGRAMMAR_BUILD_PYTHON_BINDINGS OFF)\n"
    "set(XGRAMMAR_BUILD_CXX_TESTS OFF)\n"
    "set(XGRAMMAR_ENABLE_CPPTRACE OFF)\n"
    "set(XGRAMMAR_ENABLE_COVERAGE OFF)\n"
    "set(XGRAMMAR_ENABLE_INTERNAL_CHECK OFF)\n")
add_subdirectory("${XGRAMMAR_ROOT}" "${CMAKE_CURRENT_BINARY_DIR}/xgrammar")
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    # XGrammar enables -flto=auto globally. GCC 10 cross-toolchain ar/ranlib
    # cannot index those LTO objects without the linker plugin, so the final
    # shared library silently retained unresolved XGrammar symbols. LTO is not
    # required for this embedded static dependency; compile regular objects so
    # every implementation is pulled into librkclaw_native.so reliably.
    # GCC 12 can report a false positive for vector.assign({-1}) in
    # grammar_matcher.cc. XGrammar enables -Werror, so keep the warning visible
    # without allowing this optimizer-only diagnostic to break cross builds.
    target_compile_options(xgrammar PRIVATE -fno-lto -Wno-error=array-bounds)
endif()

add_library(rkclaw_native SHARED tokenizer_bridge.cpp sampling_bridge.cpp)
target_compile_features(rkclaw_native PRIVATE cxx_std_17)
target_compile_options(rkclaw_native PRIVATE -O3 -fvisibility=hidden)
target_include_directories(rkclaw_native PRIVATE "${TOKENIZER_INCLUDE_DIR}")
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
    target_compile_options(rkclaw_native PRIVATE -march=armv8.2-a+fp16)
endif()
target_link_libraries(rkclaw_native PRIVATE
    xgrammar
    "-Wl,--whole-archive" "${TOKENIZER_LIBRARY}" "-Wl,--no-whole-archive"
    pthread
    dl)
target_link_options(rkclaw_native PRIVATE
    "-Wl,--no-undefined"
    "-Wl,--exclude-libs,ALL"
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/rkclaw_native.map")
set_target_properties(rkclaw_native PROPERTIES
    OUTPUT_NAME rkclaw_native
    LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/rkclaw_native.map")

install(TARGETS rkclaw_native LIBRARY DESTINATION lib COMPONENT native)
install(FILES tokenizer_bridge.h sampling_bridge.h DESTINATION include COMPONENT native)

if(RKCLAW_BUILD_NATIVE_TESTS)
    add_executable(rkclaw_native_test ../tests/native/test_sampling_bridge.cpp)
    target_compile_features(rkclaw_native_test PRIVATE cxx_std_17)
    target_include_directories(rkclaw_native_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(rkclaw_native_test PRIVATE rkclaw_native)
endif()
