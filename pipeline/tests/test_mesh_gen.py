import numpy as np
import pytest
from PIL import Image, ImageDraw
from mesh_gen import build_mesh

def circle_alpha():
    img = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(img).ellipse([12, 12, 51, 51], fill=255)
    return np.array(img)

def test_mesh_basics():
    mesh = build_mesh(circle_alpha(), target_cols=8)
    n = len(mesh["vertices"])
    assert n == len(mesh["uvs"]) > 0
    assert len(mesh["triangles"]) > 0
    assert all(0 <= i < n for tri in mesh["triangles"] for i in tri)
    assert all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in mesh["uvs"])
    assert mesh["imageSize"] == [64, 64]

def test_culls_far_empty_cells():
    alpha = np.zeros((64, 64), np.uint8)
    alpha[24:40, 24:40] = 255           # 16px square in the middle
    mesh = build_mesh(alpha, target_cols=8)
    # bbox 16px -> cell 2px; bbox padded 1 cell + occupancy dilated 1 cell
    # -> geometry may reach up to 2 cells outward, never the image corners
    assert all(20 <= x <= 44 and 20 <= y <= 44 for x, y in mesh["vertices"])
    # outward margin must actually exist past the tight silhouette bbox
    assert any(x < 24 for x, _ in mesh["vertices"])
    assert any(x > 40 for x, _ in mesh["vertices"])

def test_transparent_raises_valueerror():
    with pytest.raises(ValueError):
        build_mesh(np.zeros((32, 32), np.uint8), target_cols=8)
