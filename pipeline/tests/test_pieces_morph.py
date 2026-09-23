import json
import pytest
import pieces

BONES = [{"name": "root", "parent": None, "x": 10, "y": 20,
          "rotation": -90, "length": 5, "skin": False}]

def _manifest(tmp_path, frames, loop="pingpong"):
    d = {"version": 1, "pieces": [{
        "name": "body", "type": "morph", "loop": loop,
        "mesh": {"cols": 8}, "frames": frames}]}
    p = tmp_path / "pieces.json"
    p.write_text(json.dumps(d))
    return p

def _touch_frames(tmp_path, names):
    (tmp_path / "frames").mkdir(exist_ok=True)
    from PIL import Image
    for n in names:
        Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(tmp_path / "frames" / n)

def test_morph_loads(tmp_path):
    _touch_frames(tmp_path, ["a.png", "b.png", "g.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": True},
        {"file": "frames/g.png", "t": 0.1, "key": False},
        {"file": "frames/b.png", "t": 0.25, "key": True}])
    out = pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert out[0]["type"] == "morph" and len(out[0]["frames"]) == 3

def test_morph_requires_deform_allowed(tmp_path, capsys):
    # a fully VALID morph manifest must still die loud when the caller has
    # deform-class pieces disabled (same gate the deform type sits behind)
    _touch_frames(tmp_path, ["a.png", "b.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": True},
        {"file": "frames/b.png", "t": 0.25, "key": True}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=False)
    assert "morph pieces are phase 2" in capsys.readouterr().err

def test_morph_needs_two_keys(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "g.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": True},
        {"file": "frames/g.png", "t": 0.1, "key": False}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "at least 2 key frames" in capsys.readouterr().err

def test_morph_t_monotonic(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "b.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.3, "key": True},
        {"file": "frames/b.png", "t": 0.1, "key": True}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "strictly increasing" in capsys.readouterr().err

def test_morph_endpoints_must_be_keys(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "b.png", "c.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": False},
        {"file": "frames/b.png", "t": 0.1, "key": True},
        {"file": "frames/c.png", "t": 0.2, "key": True}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "first frame must be a key frame" in capsys.readouterr().err

def test_morph_last_frame_must_be_key(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "b.png", "c.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": True},
        {"file": "frames/b.png", "t": 0.1, "key": True},
        {"file": "frames/c.png", "t": 0.2, "key": False}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "last frame must be a key frame" in capsys.readouterr().err

def test_morph_bad_loop_rejected(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "b.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": True},
        {"file": "frames/b.png", "t": 0.25, "key": True}], loop="bounce")
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "loop must be one of" in capsys.readouterr().err

def test_morph_missing_frame_file_rejected(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.0, "key": True},
        {"file": "frames/missing.png", "t": 0.25, "key": True}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "file not found" in capsys.readouterr().err

def test_morph_cutting_fields_rejected(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "b.png"])
    d = {"version": 1, "pieces": [{
        "name": "body", "type": "morph", "loop": "pingpong",
        "mesh": {"cols": 8},
        "polygon": [[0, 0], [10, 0], [5, 10]],
        "frames": [
            {"file": "frames/a.png", "t": 0.0, "key": True},
            {"file": "frames/b.png", "t": 0.25, "key": True}]}]}
    p = tmp_path / "pieces.json"
    p.write_text(json.dumps(d))
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    err = capsys.readouterr().err
    assert "not allowed on morph" in err and "polygon" in err

def test_morph_duplicate_t_rejected(tmp_path, capsys):
    _touch_frames(tmp_path, ["a.png", "b.png"])
    p = _manifest(tmp_path, [
        {"file": "frames/a.png", "t": 0.1, "key": True},
        {"file": "frames/b.png", "t": 0.1, "key": True}])
    with pytest.raises(SystemExit):
        pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert "strictly increasing" in capsys.readouterr().err

def test_morph_default_loop_is_pingpong(tmp_path):
    _touch_frames(tmp_path, ["a.png", "b.png"])
    d = {"version": 1, "pieces": [{
        "name": "body", "type": "morph",
        "mesh": {"cols": 8}, "frames": [
            {"file": "frames/a.png", "t": 0.0, "key": True},
            {"file": "frames/b.png", "t": 0.25, "key": True}]}]}
    p = tmp_path / "pieces.json"
    p.write_text(json.dumps(d))
    out = pieces.load_pieces(str(p), BONES, (32, 32), deform_allowed=True)
    assert out[0]["loop"] == "pingpong"
