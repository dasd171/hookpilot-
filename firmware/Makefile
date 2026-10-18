# HookPilot firmware - HOST build (simulation + unit tests). The ESP32 target is built with
# PlatformIO (platformio.ini) and is NOT compiled by this Makefile.
#
#   make test                         build + run unit tests
#   make sim SCENARIO=bite SEED=42    print event JSON lines for one synthetic scenario
#   make verify                       validate sim output against the JSON schema + hashes (python3)
#
# No system g++ is assumed; default compiler is zig's clang via pip's ziglang.
CXX ?= python3 -m ziglang c++
# GNU make predefines CXX=g++ (origin "default"), which would defeat '?='. Override that case only.
ifeq ($(origin CXX),default)
CXX := python3 -m ziglang c++
endif

# zig targets the build machine's exact CPU by default; in some VMs that emits instructions the
# (virtualised) CPU traps on (SIGILL). Build for the baseline ISA instead.
ifneq ($(findstring ziglang,$(CXX)),)
ARCHFLAGS ?= -mcpu=baseline
endif
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter $(ARCHFLAGS)
CPPFLAGS += -Iinclude -Ihost -Isrc
BUILD := build

CORE_SRC := $(wildcard src/core/*.cpp)
HOST_SRC := host/mocks.cpp host/sim_harness.cpp
TEST_SRC := $(wildcard test_host/*.cpp)
# ESP32-only sources: compiled on host too, where their ARDUINO/ESP32 guards must reduce them to
# empty translation units (proves the host build never pulls in hardware code).
GUARD_SRC := src/main_esp32.cpp src/hw/esp32_drivers.cpp

CORE_OBJ := $(patsubst %.cpp,$(BUILD)/%.o,$(CORE_SRC))
HOST_OBJ := $(patsubst %.cpp,$(BUILD)/%.o,$(HOST_SRC))
TEST_OBJ := $(patsubst %.cpp,$(BUILD)/%.o,$(TEST_SRC))
GUARD_OBJ := $(patsubst %.cpp,$(BUILD)/%.o,$(GUARD_SRC))
SIM_OBJ  := $(BUILD)/host/sim_main.o

HEADERS := $(wildcard include/hookpilot/*.h) $(wildcard host/*.h) $(wildcard test_host/*.h) $(wildcard src/hw/*.h)

SCENARIO ?= bite
SEED ?= 42
DURATION ?= 8000

.PHONY: all test sim sim-all verify clean

all: $(BUILD)/hookpilot_tests $(BUILD)/hookpilot_sim

$(BUILD)/%.o: %.cpp $(HEADERS)
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) $(CPPFLAGS) -c $< -o $@

$(BUILD)/hookpilot_tests: $(CORE_OBJ) $(HOST_OBJ) $(TEST_OBJ) $(GUARD_OBJ)
	$(CXX) $(CXXFLAGS) $^ -o $@

$(BUILD)/hookpilot_sim: $(CORE_OBJ) $(HOST_OBJ) $(SIM_OBJ)
	$(CXX) $(CXXFLAGS) $^ -o $@

test: $(BUILD)/hookpilot_tests
	./$(BUILD)/hookpilot_tests

sim: $(BUILD)/hookpilot_sim
	@./$(BUILD)/hookpilot_sim --scenario $(SCENARIO) --seed $(SEED) --duration $(DURATION)

# All scenarios for one seed -> build/sim_<scenario>_<seed>.jsonl
sim-all: $(BUILD)/hookpilot_sim
	@for s in $$(./$(BUILD)/hookpilot_sim --list); do \
	  ./$(BUILD)/hookpilot_sim --scenario $$s --seed $(SEED) --quiet > $(BUILD)/sim_$${s}_$(SEED).jsonl; \
	  echo "$$s: $$(wc -l < $(BUILD)/sim_$${s}_$(SEED).jsonl) record(s)"; \
	done

verify: sim-all
	python3 tools/verify_events.py ../packages/event-schema/hookpilot-event.schema.json $(BUILD)/sim_*_$(SEED).jsonl

clean:
	rm -rf $(BUILD)
