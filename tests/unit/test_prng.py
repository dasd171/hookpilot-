from evidence_service.prng import Mulberry32, mulberry32

# Reference vectors computed with Node.js using the contract's JS mulberry32.
VECTORS = {
    0: [0.26642920868471265, 0.0003297457005828619, 0.2232720274478197],
    1: [0.6270739405881613, 0.002735721180215478, 0.5274470399599522],
    42: [0.6011037519201636, 0.44829055899754167, 0.8524657934904099],
    123456789: [0.2577907438389957, 0.9707721115555614, 0.7853280142880976],
    4294967295: [0.8964226141106337, 0.189478256739676, 0.7156526781618595],
}


def test_matches_js_reference_bit_exact():
    for seed, want in VECTORS.items():
        r = mulberry32(seed)
        assert [r(), r(), r()] == want


def test_seed_masked_to_32_bits():
    assert Mulberry32(2**32 + 42).next() == VECTORS[42][0]


def test_range_and_draw_counts():
    g = Mulberry32(7)
    xs = [g.next() for _ in range(10000)]
    assert all(0.0 <= x < 1.0 for x in xs)
    a, b = Mulberry32(9), Mulberry32(9)
    a.gauss()
    b.next(); b.next()
    assert a.state == b.state  # gauss consumes exactly two draws
    a.uniform(1, 2); b.next()
    assert a.state == b.state
