#include "hookpilot/json.h"

#include <cctype>
#include <cmath>
#if __has_include(<charconv>)
#include <charconv>
#endif
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace hp {

Json& Json::operator[](const std::string& key) {
  if (type_ == Type::Null) type_ = Type::Obj;
  return o_[key];
}

const Json* Json::find(const std::string& key) const {
  auto it = o_.find(key);
  return it == o_.end() ? nullptr : &it->second;
}

void Json::push(Json v) {
  if (type_ == Type::Null) type_ = Type::Arr;
  a_.push_back(std::move(v));
}

size_t Json::size() const { return type_ == Type::Arr ? a_.size() : (type_ == Type::Obj ? o_.size() : 0); }

// Shortest round-trip scientific representation "d.ddde+XX".
// Uses std::to_chars when the standard library provides floating-point to_chars (C++17, GCC>=11,
// libc++>=14); otherwise searches the smallest printf precision that round-trips (same digits,
// assuming a correctly rounding printf as in glibc/musl/newlib).
static std::string shortestScientific(double v) {
  char buf[64];
#if defined(__cpp_lib_to_chars) && __cpp_lib_to_chars >= 201611L && !defined(HP_FORCE_PRINTF_SHORTEST)
  auto res = std::to_chars(buf, buf + sizeof(buf), v, std::chars_format::scientific);
  return std::string(buf, res.ptr);
#else
  for (int prec = 0; prec <= 17; ++prec) {
    std::snprintf(buf, sizeof(buf), "%.*e", prec, v);
    if (std::strtod(buf, nullptr) == v) break;
  }
  return std::string(buf);
#endif
}

std::string jsNumber(double v) {
  if (!std::isfinite(v)) return "null";
  if (v == 0) return "0";  // also -0
  std::string sci = shortestScientific(v);
  std::string sign;
  if (sci[0] == '-') {
    sign = "-";
    sci.erase(0, 1);
  }
  const size_t epos = sci.find('e');
  std::string mant = sci.substr(0, epos);
  const int e = std::atoi(sci.c_str() + epos + 1);
  std::string digits;
  for (char c : mant)
    if (c != '.') digits += c;
  while (digits.size() > 1 && digits.back() == '0') digits.pop_back();
  const int k = static_cast<int>(digits.size());
  const int n = e + 1;
  std::string s;
  if (k <= n && n <= 21) {
    s = digits + std::string(n - k, '0');
  } else if (0 < n && n <= 21) {
    s = digits.substr(0, n) + "." + digits.substr(n);
  } else if (-6 < n && n <= 0) {
    s = "0." + std::string(-n, '0') + digits;
  } else {
    const int ex = n - 1;
    s = digits.substr(0, 1);
    if (k > 1) s += "." + digits.substr(1);
    s += "e";
    s += (ex >= 0 ? "+" : "-");
    s += std::to_string(ex >= 0 ? ex : -ex);
  }
  return sign + s;
}

std::string jsonQuote(const std::string& in) {
  std::string out = "\"";
  for (unsigned char c : in) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char b[8];
          std::snprintf(b, sizeof(b), "\\u%04x", c);
          out += b;
        } else {
          out += static_cast<char>(c);
        }
    }
  }
  out += "\"";
  return out;
}

void Json::dumpTo(std::string& out) const {
  switch (type_) {
    case Type::Null: out += "null"; break;
    case Type::Bool: out += b_ ? "true" : "false"; break;
    case Type::Int: out += std::to_string(i_); break;
    case Type::Num: out += jsNumber(d_); break;
    case Type::Str: out += jsonQuote(s_); break;
    case Type::Arr: {
      out += '[';
      for (size_t i = 0; i < a_.size(); ++i) {
        if (i) out += ',';
        a_[i].dumpTo(out);
      }
      out += ']';
      break;
    }
    case Type::Obj: {
      out += '{';
      bool first = true;
      for (const auto& kv : o_) {  // std::map: sorted by byte order
        if (!first) out += ',';
        first = false;
        out += jsonQuote(kv.first);
        out += ':';
        kv.second.dumpTo(out);
      }
      out += '}';
      break;
    }
  }
}

std::string Json::dump() const {
  std::string s;
  dumpTo(s);
  return s;
}

// ---------------- parser ----------------
namespace {
struct Parser {
  const std::string& t;
  size_t p = 0;
  std::string err;
  explicit Parser(const std::string& s) : t(s) {}
  void ws() {
    while (p < t.size() && (t[p] == ' ' || t[p] == '\n' || t[p] == '\r' || t[p] == '\t')) ++p;
  }
  bool fail(const char* m) {
    if (err.empty()) err = std::string(m) + " at " + std::to_string(p);
    return false;
  }
  bool lit(const char* s) {
    const size_t n = std::strlen(s);
    if (t.compare(p, n, s) != 0) return false;
    p += n;
    return true;
  }
  bool str(std::string& out) {
    if (t[p] != '"') return fail("expected string");
    ++p;
    while (p < t.size() && t[p] != '"') {
      char c = t[p++];
      if (c == '\\') {
        if (p >= t.size()) return fail("bad escape");
        char e = t[p++];
        switch (e) {
          case '"': out += '"'; break;
          case '\\': out += '\\'; break;
          case '/': out += '/'; break;
          case 'b': out += '\b'; break;
          case 'f': out += '\f'; break;
          case 'n': out += '\n'; break;
          case 'r': out += '\r'; break;
          case 't': out += '\t'; break;
          case 'u': {
            if (p + 4 > t.size()) return fail("bad \\u");
            unsigned cp = std::strtoul(t.substr(p, 4).c_str(), nullptr, 16);
            p += 4;
            if (cp < 0x80) out += static_cast<char>(cp);
            else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
            else { out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
            break;
          }
          default: return fail("bad escape");
        }
      } else {
        out += c;
      }
    }
    if (p >= t.size()) return fail("unterminated string");
    ++p;
    return true;
  }
  bool value(Json& out) {
    ws();
    if (p >= t.size()) return fail("unexpected end");
    const char c = t[p];
    if (c == '{') {
      ++p;
      out = Json::object();
      ws();
      if (t[p] == '}') { ++p; return true; }
      for (;;) {
        ws();
        std::string k;
        if (!str(k)) return false;
        ws();
        if (t[p] != ':') return fail("expected :");
        ++p;
        Json v;
        if (!value(v)) return false;
        out[k] = std::move(v);
        ws();
        if (t[p] == ',') { ++p; continue; }
        if (t[p] == '}') { ++p; return true; }
        return fail("expected , or }");
      }
    }
    if (c == '[') {
      ++p;
      out = Json::array();
      ws();
      if (t[p] == ']') { ++p; return true; }
      for (;;) {
        Json v;
        if (!value(v)) return false;
        out.push(std::move(v));
        ws();
        if (t[p] == ',') { ++p; continue; }
        if (t[p] == ']') { ++p; return true; }
        return fail("expected , or ]");
      }
    }
    if (c == '"') {
      std::string s;
      if (!str(s)) return false;
      out = Json(s);
      return true;
    }
    if (lit("true")) { out = Json(true); return true; }
    if (lit("false")) { out = Json(false); return true; }
    if (lit("null")) { out = Json(); return true; }
    // number
    const size_t start = p;
    bool is_int = true;
    if (t[p] == '-') ++p;
    while (p < t.size() && (std::isdigit(static_cast<unsigned char>(t[p])) || t[p] == '.' || t[p] == 'e' ||
                            t[p] == 'E' || t[p] == '+' || t[p] == '-')) {
      if (t[p] == '.' || t[p] == 'e' || t[p] == 'E') is_int = false;
      ++p;
    }
    if (p == start) return fail("unexpected character");
    const std::string num = t.substr(start, p - start);
    if (is_int && num.size() < 16) out = Json(static_cast<long long>(std::strtoll(num.c_str(), nullptr, 10)));
    else out = Json(std::strtod(num.c_str(), nullptr));
    return true;
  }
};
}  // namespace

bool parseJson(const std::string& text, Json& out, std::string* err) {
  Parser ps(text);
  const bool ok = ps.value(out);
  if (ok) {
    ps.ws();
    if (ps.p != text.size()) {
      if (err) *err = "trailing characters";
      return false;
    }
  }
  if (!ok && err) *err = ps.err;
  return ok;
}

}  // namespace hp
