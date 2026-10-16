import numpy as np
import pytest

from ailab.transforms import EPS, Transform, for_kind


def test_kind_to_transform():
    assert for_kind("count").name == "log1p"
    assert for_kind("rate").name == "log1p"
    assert for_kind("event").name == "log1p"
    assert for_kind("proportion", 100).name == "logit" and for_kind("proportion", 100).scale == 100
    with pytest.raises(ValueError):
        for_kind("bogus")
    with pytest.raises(ValueError):
        for_kind("proportion", 7)


def test_log1p_roundtrip_and_nonnegative():
    t = for_kind("count")
    y = np.array([0.0, 1.0, 10.0, 12345.0])
    assert np.allclose(t.inverse(t.forward(y)), y)
    assert (t.inverse(np.array([-50.0, -3.0, -0.1])) >= 0).all()  # never negative
    assert t.bounds == (0.0, None)


@pytest.mark.parametrize("scale", [1, 100])
def test_logit_roundtrip_bounds_and_zeros(scale):
    t = for_kind("proportion", scale)
    y = np.array([0.0, 0.01, 0.5, 0.99, 1.0]) * scale
    z = t.forward(y)
    assert np.isfinite(z).all()  # 0 and 100 % are clipped, not +-inf
    back = t.inverse(z)
    assert np.allclose(back[1:4], y[1:4])
    assert back[0] <= EPS * scale * 1.01 and back[-1] >= scale * (1 - 2 * EPS)
    wild = t.inverse(np.array([-1e9, -20.0, 20.0, 1e9]))
    assert (wild >= 0).all() and (wild <= scale).all()
    assert t.bounds == (0.0, float(scale))


def test_identity_is_passthrough():
    t = Transform("identity")
    assert np.array_equal(t.forward(np.array([-1.0, 2.0])), np.array([-1.0, 2.0]))
