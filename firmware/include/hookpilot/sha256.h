// Small self-contained SHA-256 (FIPS 180-4). No external dependencies.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace hp {

class Sha256 {
 public:
  Sha256() { reset(); }
  void reset();
  void update(const uint8_t* data, size_t len);
  void update(const std::string& s) { update(reinterpret_cast<const uint8_t*>(s.data()), s.size()); }
  void finish(uint8_t out[32]);
  std::string hexdigest();  // finishes and returns lowercase hex

 private:
  void block(const uint8_t* p);
  uint32_t h_[8];
  uint8_t buf_[64];
  size_t buf_len_ = 0;
  uint64_t total_ = 0;
};

std::string sha256Hex(const std::string& data);

}  // namespace hp
