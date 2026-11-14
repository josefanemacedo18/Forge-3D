"""Modelo de flexão do esboço (o que o Revit faz quando planos travados se movem)."""

import math

import numpy as np
import pytest

from forge3d.spec.evaluate import _flex_loop, _profile_problems, circle_from_3pts


def L(a, b):
    return {"type": "line", "start": list(a), "end": list(b)}


def A(a, m, b):
    return {"type": "arc", "start": list(a), "mid": list(m), "end": list(b)}


def rounded_rect(w=1400.0, d=800.0, r=80.0):
    """Retângulo de cantos arredondados (anti-horário), como o tampo da mesa."""
    x, y = w / 2, d / 2
    k = r * (1 - math.sqrt(0.5))
    return [
        L((-x + r, -y), (x - r, -y)),
        A((x - r, -y), (x - k, -y + k), (x, -y + r)),
        L((x, -y + r), (x, y - r)),
        A((x, y - r), (x - k, y - k), (x - r, y)),
        L((x - r, y), (-x + r, y)),
        A((-x + r, y), (-x + k, y - k), (-x, y - r)),
        L((-x, y - r), (-x, -y + r)),
        A((-x, -y + r), (-x + k, -y + k), (-x + r, -y)),
    ]


def test_tangent_corner_arcs_slide_and_keep_radius():
    loop = rounded_rect()
    # Largura 1400 -> 1050: retas laterais (eixo u = 0) se aproximam 175 mm cada.
    locks = {0: (1, 0.0), 2: (0, -175.0), 4: (1, 0.0), 6: (0, 175.0)}
    out = _flex_loop(loop, locks)
    assert not _profile_problems([out], "tampo")
    for seg in out:
        if seg["type"] == "arc":
            _, r = circle_from_3pts(np.array(seg["start"]), np.array(seg["mid"]), np.array(seg["end"]))
            assert r == pytest.approx(80.0, abs=1e-6)  # o canto só desliza
    assert out[2]["start"][0] == pytest.approx(525.0)
    assert out[0]["end"][0] == pytest.approx(445.0)  # a reta de baixo encurta junto


def test_sharp_corners_follow_both_planes():
    loop = [L((0, 0), (100, 0)), L((100, 0), (100, 50)), L((100, 50), (0, 50)), L((0, 50), (0, 0))]
    out = _flex_loop(loop, {0: (1, -10.0), 1: (0, 20.0), 2: (1, 5.0), 3: (0, 0.0)})
    assert out[1]["start"] == pytest.approx([120.0, -10.0])
    assert out[1]["end"] == pytest.approx([120.0, 55.0])
    assert not _profile_problems([out], "x")


def test_free_lines_only_stretch():
    # Chanfro (reta livre) entre a base e a lateral travadas.
    loop = [L((0, 0), (90, 0)), L((90, 0), (100, 10)), L((100, 10), (100, 50)), L((100, 50), (0, 50)),
            L((0, 50), (0, 0))]
    out = _flex_loop(loop, {0: (1, 0.0), 2: (0, 30.0), 3: (1, 0.0), 4: (0, 0.0)})
    assert out[1]["start"] == pytest.approx([90.0, 0.0])  # não é tangente: não desliza
    assert out[1]["end"] == pytest.approx([130.0, 10.0])


def test_crossing_and_short_segments_are_reported():
    loop = [L((0, 0), (100, 0)), L((100, 0), (100, 50)), L((100, 50), (0, 50)), L((0, 50), (0, 0))]
    flipped = _flex_loop(loop, {1: (0, -150.0)})  # a lateral passou da outra: perfil espelhado
    assert "invertido" in _profile_problems([flipped], "x", [loop])[0]
    bow = [L((0, 0), (100, 0)), L((100, 0), (100, 50)), L((100, 50), (50, 50)), L((50, 50), (50, 10)),
           L((50, 10), (0, 10)), L((0, 10), (0, 0))]
    crossed = _flex_loop(bow, {3: (0, 80.0)})  # o degrau atravessa a lateral
    assert "cruzado" in _profile_problems([crossed], "x", [bow])[0]
    short = _flex_loop(loop, {1: (0, -99.5)})
    assert "menor que 1 mm" in _profile_problems([short], "x")[0]


def test_unlocked_or_unmoved_loop_is_unchanged():
    loop = rounded_rect()
    assert _flex_loop(loop, {}) is loop
    assert _flex_loop(loop, {0: (1, 0.0)}) is loop
