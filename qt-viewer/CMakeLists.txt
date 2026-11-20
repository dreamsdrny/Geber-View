cmake_minimum_required(VERSION 3.21)
project(EvanGerberQtPreview LANGUAGES C CXX)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Qt6 6.5 REQUIRED COMPONENTS Widgets)
find_program(RUSTUP_EXECUTABLE rustup REQUIRED)
if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "The initial viewer target is Windows x64.")
endif()
if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    message(FATAL_ERROR "Use the Qt MinGW-w64 kit and its x86_64-w64-mingw32 compiler; MSVC/Clang kits are not configured.")
endif()

set(RUST_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/../rust/viewer_core/Cargo.toml")
set(RUST_TARGET_DIR "${CMAKE_BINARY_DIR}/rust-target")
set(RUST_TARGET "x86_64-pc-windows-gnu")
set(RUST_TOOLCHAIN "stable-x86_64-pc-windows-gnu" CACHE STRING "Installed Rust GNU toolchain name")
set(RUST_DLL "${RUST_TARGET_DIR}/${RUST_TARGET}/release/viewer_core.dll")
set(RUST_LIB "${RUST_TARGET_DIR}/${RUST_TARGET}/release/libviewer_core.dll.a")
set(RUST_STATIC_LIB "${RUST_TARGET_DIR}/${RUST_TARGET}/release/libviewer_core.a")
file(GLOB_RECURSE RUST_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/../rust/viewer_core/src/*.rs"
    "${CMAKE_CURRENT_SOURCE_DIR}/../rust/gerber_core/src/*.rs")
add_custom_command(OUTPUT "${RUST_DLL}" "${RUST_LIB}" "${RUST_STATIC_LIB}"
    COMMAND "${CMAKE_COMMAND}" -E env
            "CARGO_TARGET_DIR=${RUST_TARGET_DIR}"
            "CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=${CMAKE_C_COMPILER}"
            "${RUSTUP_EXECUTABLE}" run "${RUST_TOOLCHAIN}" cargo build
            --manifest-path "${RUST_MANIFEST}" --target "${RUST_TARGET}" --release --locked
    DEPENDS ${RUST_SOURCES} "${RUST_MANIFEST}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../rust/viewer_core/Cargo.lock"
            "${CMAKE_CURRENT_SOURCE_DIR}/../rust/gerber_core/Cargo.toml"
    VERBATIM)
add_custom_target(rust_core_build DEPENDS "${RUST_DLL}" "${RUST_LIB}" "${RUST_STATIC_LIB}")
add_library(rust_core SHARED IMPORTED GLOBAL)
set_target_properties(rust_core PROPERTIES IMPORTED_LOCATION "${RUST_DLL}" IMPORTED_IMPLIB "${RUST_LIB}")
add_dependencies(rust_core rust_core_build)

qt_add_executable(EvanGerberQtPreview WIN32 main.cpp canvas.cpp canvas.h viewer_core.h)
add_dependencies(EvanGerberQtPreview rust_core_build)
target_link_libraries(EvanGerberQtPreview PRIVATE Qt6::Widgets rust_core)
target_compile_options(EvanGerberQtPreview PRIVATE -Wall -Wextra -Wpedantic)
set_property(TARGET EvanGerberQtPreview APPEND PROPERTY LINK_OPTIONS "-mwindows")
add_custom_command(TARGET EvanGerberQtPreview POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${RUST_DLL}" "$<TARGET_FILE_DIR:EvanGerberQtPreview>"
    VERBATIM)

set(VIEWER_PACKAGE_DIR "${CMAKE_BINARY_DIR}/package")
get_filename_component(MINGW_BIN_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
add_custom_target(deploy
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${VIEWER_PACKAGE_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "$<TARGET_FILE:EvanGerberQtPreview>" "${VIEWER_PACKAGE_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${RUST_DLL}" "${VIEWER_PACKAGE_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/AGPL-3.0.txt" "${VIEWER_PACKAGE_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/THIRD-PARTY-NOTICES.txt" "${VIEWER_PACKAGE_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE" "${VIEWER_PACKAGE_DIR}/LICENSE"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../rust/viewer_core/src/lib.rs" "${VIEWER_PACKAGE_DIR}/viewer_core.rs"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../rust/viewer_core/Cargo.toml" "${VIEWER_PACKAGE_DIR}/viewer_core-Cargo.toml"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../rust/gerber_core/src/lib.rs" "${VIEWER_PACKAGE_DIR}/gerber_core.rs"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../rust/gerber_core/src/preview.rs" "${VIEWER_PACKAGE_DIR}/gerber_preview.rs"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../rust/gerber_core/Cargo.toml" "${VIEWER_PACKAGE_DIR}/gerber_core-Cargo.toml"
    COMMAND "${CMAKE_COMMAND}" -E env
            "PATH=${MINGW_BIN_DIR};$<TARGET_FILE_DIR:EvanGerberQtPreview>;${Qt6_DIR}/../../../bin;$ENV{PATH}"
            "${Qt6_DIR}/../../../bin/windeployqt.exe"
            --release --compiler-runtime --no-translations
            --dir "${VIEWER_PACKAGE_DIR}" "$<TARGET_FILE:EvanGerberQtPreview>"
    DEPENDS EvanGerberQtPreview rust_core_build
    COMMENT "Deploying Qt, Rust and MinGW runtime DLLs to ${VIEWER_PACKAGE_DIR}"
    VERBATIM)
