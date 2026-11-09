# Board programs, cross-built on the PC with cmake -S app -B build/app -G "Unix Makefiles" && cmake --build build/app
cmake_minimum_required(VERSION 3.16)
# On Windows the Vitis aarch64 Linux toolchain next to vivado cross-compiles for the KV260
if(CMAKE_HOST_WIN32)
    set(CMAKE_SYSTEM_NAME Linux)
    set(CMAKE_SYSTEM_PROCESSOR aarch64)
    if(DEFINED ENV{XILINX_VITIS})
        set(vitis "$ENV{XILINX_VITIS}")
    else()
        find_program(VIVADO NAMES vivado.bat vivado PATHS C:/AMDDesignTools/2026.1/Vivado/bin REQUIRED)
        get_filename_component(vitis "${VIVADO}/../../../Vitis" ABSOLUTE)
    endif()
    set(CMAKE_C_COMPILER "${vitis}/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe")
    set(CMAKE_MAKE_PROGRAM "${vitis}/gnuwin/bin/make.exe" CACHE FILEPATH "")
endif()
if(NOT DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS_INIT "-O2 -mcpu=cortex-a53")
endif()
project(app C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
add_compile_options(-Wall -Wextra)

# The driver, the two runners and the phrases, shared by caption and transcript
find_package(Threads REQUIRED)
add_library(common STATIC kv260.c hybrid.c cpu.c phrase.c)
target_link_libraries(common PUBLIC m Threads::Threads)

# caption prints live captions, transcript turns a WAV file into text
add_executable(caption caption.c)
target_link_libraries(caption PRIVATE common)
add_executable(transcript transcript.c)
target_link_libraries(transcript PRIVATE common)
