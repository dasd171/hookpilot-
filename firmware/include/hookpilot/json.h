// Minimal JSON value with CANONICAL serialisation (docs/SHARED_CONTRACT.md):
//   keys sorted recursively (byte order == code-point order for ASCII keys), separators ',' ':',
//   numbers formatted exactly like ECMAScript Number.prototype.toString / JSON.stringify
//   (shortest round-trip digits; 1.0 -> "1", 1e-7 -> "1e-7", 1e21 -> "1e+21"),
//   strings emitted as UTF-8 with JSON.stringify escaping (no ASCII escaping of non-ASCII).
// This matches services/evidence-service canonical.py and apps/web-demo canonical.ts.
// NaN / Infinity are not representable: dump() emits null for them (callers avoid them).
#pragma once

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace hp {

class Json {
 public:
  enum class Type { Null, Bool, Int, Num, Str, Arr, Obj };

  Json() = default;
  Json(std::nullptr_t) {}
  Json(bool b) : type_(Type::Bool), b_(b) {}
  Json(int v) : type_(Type::Int), i_(v) {}
  Json(unsigned v) : type_(Type::Int), i_(v) {}
  Json(long v) : type_(Type::Int), i_(v) {}
  Json(long long v) : type_(Type::Int), i_(v) {}
  Json(unsigned long v) : type_(Type::Int), i_(static_cast<int64_t>(v)) {}
  Json(unsigned long long v) : type_(Type::Int), i_(static_cast<int64_t>(v)) {}
  Json(double v) : type_(Type::Num), d_(v) {}
  Json(const char* s) : type_(Type::Str), s_(s) {}
  Json(const std::string& s) : type_(Type::Str), s_(s) {}

  static Json array() { Json j; j.type_ = Type::Arr; return j; }
  static Json object() { Json j; j.type_ = Type::Obj; return j; }

  Type type() const { return type_; }
  Json& operator[](const std::string& key);  // object access (converts null -> object)
  const Json* find(const std::string& key) const;
  void push(Json v);                          // array append (converts null -> array)
  size_t size() const;

  std::string dump() const;  // canonical form
  void dumpTo(std::string& out) const;

  const std::string& str() const { return s_; }
  double num() const { return type_ == Type::Int ? static_cast<double>(i_) : d_; }
  bool boolean() const { return b_; }
  const std::vector<Json>& items() const { return a_; }
  const std::map<std::string, Json>& fields() const { return o_; }

 private:
  Type type_ = Type::Null;
  bool b_ = false;
  int64_t i_ = 0;
  double d_ = 0;
  std::string s_;
  std::vector<Json> a_;
  std::map<std::string, Json> o_;
};

std::string jsNumber(double v);           // ECMAScript Number::toString
std::string jsonQuote(const std::string& s);  // JSON.stringify(string)

// Tiny strict parser (enough to round-trip our own records in tests). Returns false on error.
bool parseJson(const std::string& text, Json& out, std::string* err = nullptr);

}  // namespace hp
