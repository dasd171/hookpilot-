// Test runner: runs every registered test, prints per-test PASS/FAIL and real totals.
#include <cstdio>
#include <cstring>

#include "../host/mocks.h"
#include "test_framework.h"

int main(int argc, char** argv) {
  const char* filter = argc > 1 ? argv[1] : nullptr;
  std::printf("HookPilot firmware host unit tests (SIMULATION: mocks only, no hardware)\n");
  hp::mock::banner("MockClock");  // the remaining mock banners print once on first construction
  int passed = 0, failed = 0;
  for (auto& t : tf::registry()) {
    std::string full = std::string(t.suite) + "." + t.name;
    if (filter && full.find(filter) == std::string::npos) continue;
    tf::stats().current_failed = false;
    t.fn();
    if (tf::stats().current_failed) {
      ++failed;
      std::printf("[FAIL] %s\n", full.c_str());
    } else {
      ++passed;
      std::printf("[ OK ] %s\n", full.c_str());
    }
  }
  std::printf("\n%d tests, %d passed, %d failed; %d checks, %d failed checks\n", passed + failed, passed, failed,
              tf::stats().checks, tf::stats().failed_checks);
  return failed == 0 ? 0 : 1;
}
