# Builds this repository's standalone Linux TCP benchmark
# and registers its integration test.

cmake_minimum_required(VERSION 3.20)

project(
    network_performance_framework
    VERSION 0.1.0
    LANGUAGES CXX
)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(
        FATAL_ERROR
        "The TCP benchmark currently requires Linux."
    )
endif()

find_package(Threads REQUIRED)

add_executable(
    tcp_roundtrip
    benchmarks/tcp_roundtrip.cpp
)

target_compile_features(
    tcp_roundtrip
    PRIVATE cxx_std_17
)

target_compile_options(
    tcp_roundtrip
    PRIVATE
    -Wall
    -Wextra
    -Wpedantic
    -Wconversion
    -Wshadow
)

target_link_libraries(
    tcp_roundtrip
    PRIVATE Threads::Threads
)

include(CTest)

if(BUILD_TESTING)
    find_package(
        Python3
        REQUIRED COMPONENTS Interpreter
    )

    add_test(
        NAME tcp_benchmark_integration
        COMMAND
            "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/benchmarks/check_tcp_benchmark.py"
            "$<TARGET_FILE:tcp_roundtrip>"
    )

    set_tests_properties(
        tcp_benchmark_integration
        PROPERTIES TIMEOUT 60
    )
endif()