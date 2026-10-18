// Minimal self-contained unit-test framework (no external dependencies).
#pragma once

#include <cmath>
#include <cstdio>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

namespace tf {

struct TestCase {
  const char* suite;
  const char* name;
  std::function<void()> fn;
};

inline std::vector<TestCase>& registry() {
  static std::vector<TestCase> r;
  return r;
}

struct Stats {
  int checks = 0;
  int failed_checks = 0;
  bool current_failed = false;
};
inline Stats& stats() {
  static Stats s;
  return s;
}

struct Registrar {
  Registrar(const char* suite, const char* name, std::function<void()> fn) {
    registry().push_back({suite, name, std::move(fn)});
  }
};

inline void report(bool ok, const char* file, int line, const std::string& what) {
  ++stats().checks;
  if (!ok) {
    ++stats().failed_checks;
    stats().current_failed = true;
    std::printf("    FAILED %s:%d: %s\n", file, line, what.c_str());
  }
}

template <typename A, typename B>
std::string eqMsg(const char* ea, const char* eb, const A& a, const B& b) {
  std::ostringstream os;
  os << ea << " == " << eb << "  (got '" << a << "' vs '" << b << "')";
  return os.str();
}

}  // namespace tf

#define TF_CAT2(a, b) a##b
#define TF_CAT(a, b) TF_CAT2(a, b)
#define TEST(suite, name)                                                              \
  static void TF_CAT(test_##suite##_, name)();                                         \
  static tf::Registrar TF_CAT(reg_##suite##_, name)(#suite, #name, TF_CAT(test_##suite##_, name)); \
  static void TF_CAT(test_##suite##_, name)()

#define CHECK(cond) tf::report(static_cast<bool>(cond), __FILE__, __LINE__, #cond)
#define CHECK_EQ(a, b)                                                       \
  do {                                                                       \
    const auto& _a = (a);                                                    \
    const auto& _b = (b);                                                    \
    tf::report(_a == _b, __FILE__, __LINE__, tf::eqMsg(#a, #b, _a, _b));     \
  } while (0)
#define CHECK_NEAR(a, b, tol)                                                          \
  do {                                                                                 \
    const double _a = (a), _b = (b);                                                   \
    std::ostringstream _os;                                                            \
    _os.precision(17);                                                                 \
    _os << #a << " ~= " << #b << " (got " << _a << " vs " << _b << ", tol " << (tol) << ")"; \
    tf::report(std::fabs(_a - _b) <= (tol), __FILE__, __LINE__, _os.str());            \
  } while (0)
