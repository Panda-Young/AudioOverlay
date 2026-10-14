cmake_minimum_required(VERSION 3.22.1)

project("audiooverlay")

add_library(
    bgmprocessor
    SHARED
    BgmProcessor.cpp
    native-lib.cpp
    mdstft.cpp
)

find_library(
    log-lib
    log
)

# FFTW3 static lib (float) — prebuilt for arm64-v8a only
if(ANDROID_ABI STREQUAL "arm64-v8a")
    target_include_directories(bgmprocessor PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(
        bgmprocessor
        ${log-lib}
        ${CMAKE_CURRENT_SOURCE_DIR}/prebuilt/${ANDROID_ABI}/libfftw3f.a
    )
else()
    target_link_libraries(
        bgmprocessor
        ${log-lib}
    )
endif()
