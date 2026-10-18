// sha256, canonical JSON, PRNG and synthetic-scenario reproducibility.
#include <cmath>
#include <limits>

#include "hookpilot/event_logger.h"
#include "hookpilot/json.h"
#include "hookpilot/prng.h"
#include "hookpilot/scenarios.h"
#include "hookpilot/sha256.h"
#include "test_framework.h"

using namespace hp;

TEST(sha256, known_vectors) {
  CHECK_EQ(sha256Hex(""), std::string("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
  CHECK_EQ(sha256Hex("abc"), std::string("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
  CHECK_EQ(sha256Hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
           std::string("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));
  Sha256 h;
  std::string chunk(1000, 'a');
  for (int i = 0; i < 1000; ++i) h.update(chunk);
  CHECK_EQ(h.hexdigest(), std::string("cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"));
}

TEST(json, js_number_formatting_matches_ecmascript) {
  CHECK_EQ(jsNumber(1.0), std::string("1"));
  CHECK_EQ(jsNumber(-0.0), std::string("0"));
  CHECK_EQ(jsNumber(0.1), std::string("0.1"));
  CHECK_EQ(jsNumber(2.4019), std::string("2.4019"));
  CHECK_EQ(jsNumber(1e-7), std::string("1e-7"));
  CHECK_EQ(jsNumber(1.5e-6), std::string("0.0000015"));
  CHECK_EQ(jsNumber(0.000001), std::string("0.000001"));
  CHECK_EQ(jsNumber(1e21), std::string("1e+21"));
  CHECK_EQ(jsNumber(1e20), std::string("100000000000000000000"));
  CHECK_EQ(jsNumber(123456789.125), std::string("123456789.125"));
  CHECK_EQ(jsNumber(-12.5), std::string("-12.5"));
  CHECK_EQ(jsNumber(0.30000000000000004), std::string("0.30000000000000004"));
  CHECK_EQ(jsNumber(std::numeric_limits<double>::quiet_NaN()), std::string("null"));
}

TEST(json, canonical_sorted_compact_and_roundtrip) {
  Json j = Json::object();
  j["b"] = 1;
  j["a"]["z"] = "x\"y\n";
  j["a"]["c"] = 2.5;
  j["arr"].push(true);
  j["arr"].push(Json());
  CHECK_EQ(j.dump(), std::string("{\"a\":{\"c\":2.5,\"z\":\"x\\\"y\\n\"},\"arr\":[true,null],\"b\":1}"));
  Json back;
  CHECK(parseJson(j.dump(), back));
  CHECK_EQ(back.dump(), j.dump());
}

TEST(json, record_hash_is_reproducible_and_detects_tamper) {
  EventRecord r;
  r.event_id = "dev-000001-deadbeef";
  r.device_id = "dev";
  r.stop_reason = "test";
  r.classification.reason = "unit";
  Json j = recordToJson(r);
  const std::string h1 = computeRecordHash(j);
  const std::string h2 = computeRecordHash(recordToJson(r));
  CHECK_EQ(h1, h2);
  CHECK_EQ(h1.size(), size_t(64));
  r.stop_reason = "tampered";
  CHECK(computeRecordHash(recordToJson(r)) != h1);
}

TEST(time, utc_formatting) {
  CHECK_EQ(formatUtc(kSimulatedEpochMs), std::string("2026-01-01T00:00:00.000Z"));
  CHECK_EQ(formatUtc(kSimulatedEpochMs + 2160 + 86400000LL * 59), std::string("2026-03-01T00:00:02.160Z"));
  CHECK_EQ(formatUtc(951782400000LL), std::string("2000-02-29T00:00:00.000Z"));
}

// Reference values computed with services/evidence-service (Python port of the JS reference).
TEST(prng, mulberry32_matches_reference_vectors) {
  Mulberry32 g(42);
  const uint32_t ref42[5] = {2581720956u, 1925393290u, 3661312704u, 2876485805u, 750819978u};
  for (uint32_t v : ref42) CHECK_EQ(g.nextU32(), v);
  Mulberry32 z(0);
  const uint32_t ref0[3] = {1144304738u, 1416247u, 958946056u};
  for (uint32_t v : ref0) CHECK_EQ(z.nextU32(), v);
  Mulberry32 m(0xFFFFFFFFu);
  const uint32_t refm[3] = {3850105811u, 813802916u, 3073704848u};
  for (uint32_t v : refm) CHECK_EQ(m.nextU32(), v);
  Mulberry32 q(42);
  CHECK_NEAR(q.gauss(), -0.956162229384149, 1e-12);
  CHECK_NEAR(q.uniform(1, 2), 1.8524657934904099, 1e-15);
}

TEST(scenarios, synth_matches_python_reference_seed42) {
  struct Ref { const char* sc; double x0, x150, x399; };
  const Ref refs[] = {
      {"bite", 0.440177301366037, 0.8494166716211489, 0.4418697556582504},
      {"noise_current", 0.4033330190923526, 0.8408704704259676, 1.993138233180296},
      {"rod_vibration", 0.4368714847354783, 1.5598834506572388, 0.43292966336908617},
      {"manual_handling", 0.4368714847354783, 11.633885797127945, 0.43292966336908617},
      {"sensor_failure", 0.4033330190923526, 0.4616954973958287, 0.4111655983879681},
      {"excessive_tension", 0.41734792136894405, 2.692211007534275, 15.444849172965503},
      {"motor_stall", 0.440177301366037, 2.52951609034217, 0.4418697556582504},
      {"comm_loss", 0.440177301366037, 2.52951609034217, 0.4418697556582504},
      {"idle", 0.43470504069037236, 0.423842514166889, 0.42819488911200243},
  };
  for (const auto& r : refs) {
    SynthSignal s = generateSynth(r.sc, 42);
    CHECK_EQ(s.tension_n.size(), size_t(400));
    CHECK_NEAR(s.tension_n[0], r.x0, 1e-9);
    CHECK_NEAR(s.tension_n[150], r.x150, 1e-9);
    CHECK_NEAR(s.tension_n[399], r.x399, 1e-9);
    CHECK_NEAR(s.params.onset_ms, 2160.0, 0);
  }
}

TEST(scenarios, deterministic_and_seed_sensitive) {
  for (const auto& sc : synthScenarios()) {
    SynthSignal a = generateSynth(sc, 1234), b = generateSynth(sc, 1234), c = generateSynth(sc, 1235);
    CHECK(a.tension_n == b.tension_n);
    CHECK(a.tension_n != c.tension_n);
    // Longer windows only append samples (draw order is params, then per-sample noise).
    SynthSignal l = generateSynth(sc, 1234, 50.0, 12000);
    bool prefix = true;
    for (size_t i = 0; i < a.tension_n.size(); ++i) prefix = prefix && a.tension_n[i] == l.tension_n[i];
    if (sc != "sensor_failure") CHECK(prefix);  // out_of_range window is clipped to n; stuck runs to end
  }
  bool threw = false;
  try {
    generateSynth("not_a_scenario", 1);
  } catch (const std::invalid_argument&) {
    threw = true;
  }
  CHECK(threw);
}
