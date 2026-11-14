import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from forge3d.errors import EngineError
from forge3d.image.pipeline_image import ImageOptions, analyze_image, convert_image
from forge3d.spec import validate


def vase_image(path):
    img = Image.new("RGB", (600, 800), (245, 245, 242))
    d = ImageDraw.Draw(img)
    pts = []
    for y in range(100, 700):
        t = (y - 100) / 600
        pts.append((300 + 90 + 60 * math.sin(t * math.pi) + 20 * t, y))
    d.polygon(pts + [(600 - x, y) for x, y in reversed(pts)], fill=(120, 70, 40))
    arr = np.asarray(img).astype(float) + np.random.default_rng(0).normal(0, 3, (800, 600, 3))
    Image.fromarray(arr.clip(0, 255).astype(np.uint8)).save(path, quality=88)


def chair_image(path):
    img = Image.new("RGB", (600, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([150, 100, 450, 420], fill=(60, 60, 60))
    d.rectangle([140, 420, 460, 460], fill=(60, 60, 60))
    d.rectangle([150, 460, 185, 750], fill=(60, 60, 60))
    d.rectangle([415, 460, 450, 750], fill=(60, 60, 60))
    img.save(path)


def test_vase_becomes_revolution_with_real_scale(tmp_path):
    p = tmp_path / "vaso.jpg"
    vase_image(p)
    a = analyze_image(str(p), ImageOptions(), str(tmp_path))
    assert a["suggestedStrategy"] == "revolucao"
    res = convert_image(str(p), ImageOptions(reference_mm=450), work_dir=str(tmp_path))
    validate.validate(res.spec)
    assert res.summary["strategy"] == "revolucao"
    assert res.spec["envelope"]["height"] == pytest.approx(450, abs=1)


def test_front_view_chair_is_not_revolution(tmp_path):
    p = tmp_path / "cadeira.png"
    chair_image(p)
    res = convert_image(str(p), ImageOptions(reference_mm=900, depth_mm=480), work_dir=str(tmp_path))
    validate.validate(res.spec)
    assert res.summary["strategy"] == "extrusao"
    assert res.spec["family"]["achievedLevel"] == "C"
    assert res.spec["envelope"]["depth"] == pytest.approx(480, abs=1)


def test_missing_reference_warns(tmp_path):
    p = tmp_path / "vaso.jpg"
    vase_image(p)
    res = convert_image(str(p), ImageOptions(), work_dir=str(tmp_path))
    assert any("referência" in w for w in res.spec["report"]["warnings"])


def test_blank_image_is_rejected(tmp_path):
    p = tmp_path / "branco.png"
    Image.new("RGB", (300, 300), (255, 255, 255)).save(p)
    with pytest.raises(EngineError):
        convert_image(str(p), ImageOptions(reference_mm=100), work_dir=str(tmp_path))


def test_ai_strategy_without_module_gives_clear_error(tmp_path):
    p = tmp_path / "vaso.jpg"
    vase_image(p)
    with pytest.raises(EngineError, match="IA"):
        convert_image(str(p), ImageOptions(strategy="ia", reference_mm=450), work_dir=str(tmp_path))
