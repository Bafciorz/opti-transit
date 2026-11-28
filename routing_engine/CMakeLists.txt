cmake_minimum_required(VERSION 3.20)
project(opti_transit_routing LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Core grafu CSR i GraphBuilder (niezależny od PostgreSQL — łatwy do testowania)
add_library(opti_routing_core STATIC
    src/graph.cpp
    src/graph_builder.cpp
)
target_include_directories(opti_routing_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)
if (MSVC)
    target_compile_options(opti_routing_core PRIVATE /W4)
else()
    target_compile_options(opti_routing_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

# Testy jednostkowe (bez zewnętrznych zależności sieciowych)
enable_testing()
add_executable(opti_routing_tests
    tests/test_graph_builder.cpp
)
target_link_libraries(opti_routing_tests PRIVATE opti_routing_core)
add_test(NAME GraphBuilderTests COMMAND opti_routing_tests)

# Loader PostgreSQL + CLI budowania grafu z opti_transit_pipeline (opcjonalnie, gdy dostępny libpq)
find_package(PostgreSQL QUIET)
if (PostgreSQL_FOUND)
    message(STATUS "PostgreSQL libpq found: building opti_pg_loader and opti_graph_builder_cli")

    add_library(opti_pg_loader STATIC
        src/pg_loader.cpp
    )
    target_link_libraries(opti_pg_loader PUBLIC
        opti_routing_core
        PostgreSQL::PostgreSQL
    )

    add_executable(opti_graph_builder_cli
        src/main.cpp
    )
    target_link_libraries(opti_graph_builder_cli PRIVATE opti_pg_loader)
else()
    message(WARNING "PostgreSQL libpq NOT found — skipping opti_graph_builder_cli (core & unit tests still build)")
endif()
