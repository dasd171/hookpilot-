#!/usr/bin/env sh
# Fallback host build for machines without GNU make. Same sources/flags as the Makefile.
#   tools/host_build.sh            build + run unit tests
#   tools/host_build.sh sim bite 42
set -e
cd "$(dirname "$0")/.."
CXX="${CXX:-python3 -m ziglang c++}"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -Iinclude -Ihost -Isrc"
case "$CXX" in *ziglang*) FLAGS="$FLAGS -mcpu=baseline" ;; esac
mkdir -p build
CORE="src/core/*.cpp host/mocks.cpp host/sim_harness.cpp"
if [ "$1" = "sim" ]; then
  $CXX $FLAGS $CORE host/sim_main.cpp -o build/hookpilot_sim
  exec ./build/hookpilot_sim --scenario "${2:-bite}" --seed "${3:-42}"
fi
$CXX $FLAGS $CORE src/main_esp32.cpp src/hw/esp32_drivers.cpp test_host/*.cpp -o build/hookpilot_tests
exec ./build/hookpilot_tests
