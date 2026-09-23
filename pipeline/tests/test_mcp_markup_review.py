"""markup review gate MCP tools — session/result file protocol.

Loads mcp/server.py by file path (mcp/ is not a package and must not shadow
the pip `mcp` SDK), same as test_mcp_guide_topics.py."""
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image as PILImage

REPO = Path(__file__).resolve().parents[2]


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "textrig_mcp_server_markup", REPO / "mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _load_server()
ToolError = server.ToolError

SKELETON = [
    {"name": "root", "parent": None, "x": 5, "y": 9, "rotation": -90, "length": 2},
    {"name": "head", "parent": "root", "x": 5, "y": 3, "rotation": -90, "length": 2},
]
PIECES = {"version": 1, "pieces": [
    {"name": "body", "bone": "root", "type": "rigid",
     "source": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}},
]}


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    """Isolated OUT dir + a real small image + no dev-server side effects."""
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(server, "OUT", out)
    monkeypatch.setattr(server, "_ensure_dev_server", lambda *a, **k: None)
    monkeypatch.setattr(server, "_open_in_browser", lambda *a, **k: None)
    img = tmp_path / "src.jpg"
    PILImage.new("RGB", (10, 10), (200, 30, 30)).save(img)
    return out, img


def _open(out, img, name="t", skeleton=SKELETON, pieces=PIECES):
    return server.open_markup_review(name, str(img), skeleton, pieces)


def test_open_writes_session_and_rgba_png(gate):
    out, img = gate
    msg = _open(out, img)
    assert "markup.html?name=t" in msg
    session = json.loads((out / "markup-t.session.json").read_text())
    assert session["name"] == "t" and len(session["token"]) >= 8
    assert session["imageSize"] == [10, 10]
    assert session["skeleton"] == SKELETON and session["pieces"] == PIECES
    with PILImage.open(out / "markup-t.png") as im:
        assert im.mode == "RGBA" and im.size == (10, 10)


def test_open_object_form_skeleton_roundtrips(gate):
    out, img = gate
    obj_skel = {"bones": SKELETON, "paths": []}
    _open(out, img, skeleton=obj_skel)
    session = json.loads((out / "markup-t.session.json").read_text())
    assert session["skeleton"] == obj_skel


def test_open_and_await_with_no_pieces(gate):
    out, img = gate
    _open(out, img, pieces=None)
    session = json.loads((out / "markup-t.session.json").read_text())
    assert session["pieces"] is None
    token = session["token"]
    (out / "markup-t.result.json").write_text(json.dumps(
        {"token": token, "skeleton": SKELETON, "pieces": None,
         "meta": {"new_pieces": [], "user_note": ""}}))
    got = server.await_markup_review("t", timeout_s=0)
    payload = json.loads(got[:got.rindex("}") + 1])
    assert payload["pieces"] is None
    assert "No new pieces" in got


def test_open_unreadable_image_warns_loudly(gate):
    out, _ = gate
    fake = out.parent / "notimage.png"
    fake.write_text("this is not an image")
    msg = server.open_markup_review("t", str(fake), SKELETON, PIECES)
    assert "WARNING" in msg
    session = json.loads((out / "markup-t.session.json").read_text())
    assert session["imageSize"] is None
    assert (out / "markup-t.png").read_text() == "this is not an image"


def test_open_deletes_stale_result(gate):
    out, img = gate
    (out / "markup-t.result.json").write_text("{}")
    _open(out, img)
    assert not (out / "markup-t.result.json").exists()


@pytest.mark.parametrize("name", ["a b", "a/b", "", "a?b", "имя", "café"])
def test_open_rejects_bad_name(gate, name):
    out, img = gate
    with pytest.raises(ToolError):
        _open(out, img, name=name)


@pytest.mark.parametrize("name", ["x_ske", "x_tex"])
def test_open_rejects_ske_tex_suffix(gate, name):
    out, img = gate
    with pytest.raises(ToolError, match="_ske/_tex"):
        _open(out, img, name=name)


def test_open_rejects_bad_markup(gate):
    out, img = gate
    dup = SKELETON + [{"name": "head", "parent": "root", "x": 1, "y": 1}]
    with pytest.raises(ToolError, match="duplicate"):
        _open(out, img, skeleton=dup)
    two_roots = [dict(SKELETON[0]), dict(SKELETON[0], name="root2")]
    with pytest.raises(ToolError, match="root"):
        _open(out, img, skeleton=two_roots)
    with pytest.raises(ToolError, match="polygon"):
        _open(out, img, pieces={"pieces": [{"name": "p", "source": {"polygon": [[0, 0], [1, 1]]}}]})
    with pytest.raises(ToolError, match="absolute"):
        server.open_markup_review("t", "rel/img.png", SKELETON, None)


def test_await_without_open_is_error(gate):
    with pytest.raises(ToolError, match="open_markup_review"):
        server.await_markup_review("nope", timeout_s=0)


def test_await_pending_then_result_then_idempotent(gate):
    out, img = gate
    _open(out, img)
    token = json.loads((out / "markup-t.session.json").read_text())["token"]

    pending = server.await_markup_review("t", timeout_s=0)
    assert "still editing" in pending

    edited = {"token": token, "skeleton": SKELETON, "pieces": PIECES,
              "meta": {"new_pieces": ["body_cut1"], "user_note": "hi"}}
    (out / "markup-t.result.json").write_text(json.dumps(edited))
    got = server.await_markup_review("t", timeout_s=0)
    assert "body_cut1" in got and "hi" in got
    assert json.loads(got[:got.rindex("}") + 1])  # leading pretty JSON parses

    assert "body_cut1" in server.await_markup_review("t", timeout_s=0)  # idempotent


def test_await_ignores_stale_token(gate):
    out, img = gate
    _open(out, img)
    (out / "markup-t.result.json").write_text(json.dumps(
        {"token": "old", "skeleton": [], "pieces": None, "meta": {}}))
    msg = server.await_markup_review("t", timeout_s=0)
    assert "still editing" in msg and "OLDER" in msg
