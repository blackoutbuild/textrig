"""Guard: the MCP guide topic map stays in sync with the docs it slices.

Loads mcp/server.py by file path (the repo's mcp/ dir is NOT a package and
must not shadow the pip `mcp` SDK the server imports)."""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "textrig_mcp_server", REPO / "mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _load_server()


# ---------------------------------------------------------------- _section

DOC = """\
# Title

intro text

## 1. Alpha

alpha body

### Alpha sub

sub body

## 2. Beta

```sh
# a comment inside a fence must not terminate a section
```

beta body
"""


@pytest.fixture()
def doc(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text(DOC)
    return p


def test_section_same_level_includes_subsections(doc):
    out = server._section(doc, "## 1. Alpha", "same")
    assert "alpha body" in out and "### Alpha sub" in out and "sub body" in out
    assert "beta body" not in out


def test_section_until_any_stops_before_subsection(doc):
    out = server._section(doc, "## 1. Alpha", "any")
    assert "alpha body" in out
    assert "### Alpha sub" not in out


def test_section_h1_until_any_is_intro(doc):
    out = server._section(doc, "# Title", "any")
    assert "intro text" in out
    assert "## 1. Alpha" not in out


def test_section_ignores_hashes_inside_code_fences(doc):
    out = server._section(doc, "## 2. Beta", "same")
    assert "a comment inside a fence" in out and "beta body" in out


def test_section_none_heading_returns_whole_file(doc):
    assert server._section(doc, None, "same") == DOC


def test_section_missing_heading_raises(doc):
    with pytest.raises(ValueError, match="heading not found"):
        server._section(doc, "## Nope", "same")


# ---------------------------------------------------------------- topics


def test_every_topic_renders_nonempty():
    for topic in ("core", "pieces", "example", "all"):
        out = server._guide(topic)
        assert len(out) > 500, f"topic {topic!r} suspiciously small"


def test_core_has_the_always_needed_material():
    core = server._guide("core")
    for marker in (
        "# MCP addendum",
        "# Choosing the rig type",
        "## 3. Skeleton archetypes",
        "## 5. Animation vocabulary",
        "## 6. Gotchas",
        "# Inspection checklist",
        "# TextRig format v1",
        'rigging_guide(topic="pieces")',   # the closing TOC
    ):
        assert marker in core, f"core is missing {marker!r}"


def test_core_excludes_pieces_only_material():
    core = server._guide("core")
    for marker in (
        "## 2. Polygon heuristics",
        "## 4. Binding-type decision rules",
        "### Depth changes (order tracks)",
        "## 7. Error → knob map",
        "## Piece tracks (multi-piece rigs",
    ):
        assert marker not in core, f"core leaked pieces-only section {marker!r}"


def test_pieces_topic_has_the_cutting_rules():
    pieces = server._guide("pieces")
    for marker in (
        "## 2. Polygon heuristics",
        "## 4. Binding-type decision rules",
        "### Depth changes (order tracks)",
        "## 7. Error → knob map",
        "## Piece tracks (multi-piece rigs",
    ):
        assert marker in pieces, f"pieces topic missing {marker!r}"


def test_example_topic_is_the_worked_robot_rig():
    example = server._guide("example")
    for marker in ("skeleton.json", "pieces.json", "animations.json",
                   '"arm_r"', '"eye_glow"'):
        assert marker in example


def test_all_superset_and_core_actually_smaller():
    all_ = server._guide("all")
    for marker in ("## 2. Polygon heuristics", "## 3. Skeleton archetypes",
                   "## Piece tracks (multi-piece rigs", "skeleton.json"):
        assert marker in all_
    core = server._guide("core")
    assert len(core) < 0.75 * len(all_), (
        f"core ({len(core)}) is not meaningfully smaller than all ({len(all_)})")


def test_unknown_topic_raises_tool_error():
    with pytest.raises(server.ToolError, match="unknown rigging_guide topic"):
        server._guide("nope")


# ---------------------------------------------------------------- dims


def test_image_dims_reads_real_formats(tmp_path):
    from PIL import Image
    for name in ("t.png", "t.jpg", "t.webp"):
        p = tmp_path / name
        Image.new("RGB", (12, 34)).save(p)
        assert server._image_dims(p) == "12x34px"


def test_image_dims_unreadable_is_soft(tmp_path):
    p = tmp_path / "not-an-image.png"
    p.write_text("junk")
    assert server._image_dims(p) == "unknown size"


# ------------------------------------------------------- error wrapping


def test_build_failure_error_carries_image_dims(tmp_path, monkeypatch):
    from PIL import Image
    img = tmp_path / "src.png"
    Image.new("RGBA", (20, 40)).save(img)

    def boom(*args, **kwargs):
        raise server.ToolError("validator says boom")

    monkeypatch.setattr(server, "_run", boom)
    with pytest.raises(server.ToolError, match=r"validator says boom") as exc_info:
        server.build_rig("wrap-test", str(img), [], {})
    assert "[source image src.png: 20x40px]" in str(exc_info.value)


# ---------------------------------------------------------------- region


def test_render_region_zoom_validated():
    with pytest.raises(server.ToolError, match="zoom"):
        server.render("no-such-rig", zoom=0)
    with pytest.raises(server.ToolError, match="zoom"):
        server.render("no-such-rig", zoom=99)


def test_render_region_focus_validated():
    with pytest.raises(server.ToolError, match="fx/fy"):
        server.render("no-such-rig", zoom=2, fx=1.5)
    with pytest.raises(server.ToolError, match="fx/fy"):
        server.render("no-such-rig", fy=-0.1)


def test_render_region_reaches_cmd_positionally(monkeypatch):
    captured = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        raise server.ToolError("stop here")

    monkeypatch.setattr(server, "_run", fake_run)
    # bypass the built-triple check
    monkeypatch.setattr(server, "_triple_paths",
                        lambda name: [Path(__file__)] * 3)
    with pytest.raises(server.ToolError, match="stop here"):
        server.render("robot", zoom=2, fx=0.85, fy=0.2)
    i = captured["cmd"].index("mcp-robot")
    assert captured["cmd"][i + 1:i + 4] == ["2.0", "0.85", "0.2"]
