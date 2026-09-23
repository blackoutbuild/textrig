"""Tests for the local TextRig MCP server (mcp/server.py), driven through the
official SDK's in-memory client/server session — no subprocess, no Claude.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from mcp.shared.memory import create_connected_server_and_client_session

REPO = Path(__file__).resolve().parents[2]


def _load_server():
    """Load mcp/server.py by file path, as test_mcp_guide_topics.py does.

    Not `import server`: mcp/ is not a package, and the top-level name
    `server` would be ambiguous on sys.path.
    """
    spec = importlib.util.spec_from_file_location(
        "textrig_mcp_server_tools", REPO / "mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


textrig_server = _load_server()  # the FastMCP instance lives at .mcp

pytestmark = pytest.mark.anyio

NODE_MISSING = not (REPO / "renderer" / "node_modules").is_dir()

FIX = Path(__file__).resolve().parent / "fixtures" / "zorder"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _zorder_inputs():
    """Fresh copies of the zorder fixture's JSON documents (dicts, not files) —
    tools take JSON values as arguments, not paths."""
    skeleton = json.loads((FIX / "skeleton.json").read_text())
    animations = json.loads((FIX / "animations.json").read_text())
    pieces = json.loads((FIX / "pieces.json").read_text())
    return skeleton, animations, pieces


async def test_rigging_guide_returns_our_docs():
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("rigging_guide", {})
        text = res.content[0].text
        assert "inspection" in text.lower() and "skeleton" in text.lower()
        assert len(text) > 5000            # real docs, not a stub


async def test_rigging_guide_starts_with_mcp_addendum():
    # The docs are written for the in-repo loop (file paths, shell commands,
    # pixel probes). The guide must open with an MCP addendum that re-frames
    # them for a repo-blind session BEFORE any doc content appears.
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("rigging_guide", {})
        text = res.content[0].text
        assert "MCP addendum" in text
        assert text.index("MCP addendum") < text.index("rigging-playbook.md")
        low = text.lower()
        assert "do not apply" in low            # path/shell refs disclaimed
        assert "passed directly to build_rig" in low
        assert "render(" in text                # image inspection replaces probes
        # variant/source `file` entries: the worked example's relative paths
        # are repo-internal; the MCP consumer must use absolute paths and save
        # generated variant PNGs next to the source image.
        assert "variants" in low
        assert "saved beside the source image" in low
        # the markup debug view is delivered by build_rig itself
        assert "comes back from build_rig" in low


async def test_rigging_guide_contains_inline_worked_example():
    # A complete pieces.json example must exist IN the guide (the foreign
    # session cannot open examples/robot/). Served at call time, labeled.
    # Layered guide (topic="core"/"pieces"/"example"/"all"): the worked
    # example lives at topic="example" (and inside topic="all"), not in the
    # default core guide — see test_mcp_guide_topics.py for the topic split.
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("rigging_guide", {"topic": "example"})
        text = res.content[0].text
        assert "Worked example" in text
        for fname in ("skeleton.json", "pieces.json", "animations.json"):
            assert (REPO / "examples" / "robot" / fname).read_text().strip() in text, \
                f"robot {fname} content not served inline"


async def test_build_rig_happy_path_zorder_fixture():
    skeleton, animations, pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
        })
        assert not res.isError, res.content[0].text
        assert (REPO / "out" / "mcptest_ske.json").is_file()
        # A pieces build returns the piece-cuts/bones debug view inline so
        # the repo-blind agent can check its polygons BEFORE animating.
        images = [c for c in res.content if c.type == "image"]
        assert len(images) == 1
        assert images[0].mimeType == "image/png"
        assert len(images[0].data) > 0
        texts = [c.text for c in res.content if c.type == "text"]
        assert any("debug" in t.lower() for t in texts)   # captioned, not bare


async def test_build_rig_accepts_object_form_skeleton_with_weighted_path():
    """The `skeleton` param takes the {"bones", "paths"} object form — the
    only route to path constraints and wave tracks for the MCP consumer
    (pydantic once rejected the object before the pipeline ever saw it)."""
    skeleton, animations, pieces = _zorder_inputs()
    root = skeleton[0]["name"]
    skeleton_obj = {
        "bones": skeleton + [
            {"name": "s1", "parent": root, "x": 40, "y": 30, "rotation": -90, "length": 10},
            {"name": "s2", "parent": "s1", "x": 40, "y": 20, "rotation": -90, "length": 10},
        ],
        "paths": [{"name": "sway", "bone": root, "weighted": True,
                   "points": [[40, 30], [42, 20], [40, 10]], "chain": ["s1", "s2"]}],
    }
    animations["flip"]["tracks"].append(
        {"path": "sway", "prop": "wave", "amplitude": 2, "period": 1.0})
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_paths",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton_obj,
            "animations": animations,
            "pieces": pieces,
        })
        assert not res.isError, res.content[0].text
        ske = json.loads((REPO / "out" / "mcptest_paths_ske.json").read_text())
        arm = ske["armature"][0]
        assert arm["path"][0]["name"] == "sway"                      # constraint emitted
        bone_names = {b["name"] for b in arm["bone"]}
        assert {"sway_a0", "sway_a1", "sway_a2"} <= bone_names       # weighted anchors
        anim = arm["animation"][0]
        timeline_bones = {tl["name"] for tl in anim["bone"]}
        assert "sway_a2" in timeline_bones                           # wave expanded
        texts = [c.text for c in res.content if c.type == "text"]
        assert any("3 bone(s)" in t for t in texts)                  # summary counts bones[]


async def test_build_rig_reports_validator_error_verbatim():
    # break the zorder fixture's order track (out-of-range shift) → the loud
    # validator/writer message must come back as the tool result, isError=True
    skeleton, animations, pieces = _zorder_inputs()
    animations["flip"]["tracks"][0]["keys"][1]["v"] = 99
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_bad",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
        })
        assert res.isError
        assert "out of range" in res.content[0].text


async def test_build_rig_rejects_relative_image_path():
    skeleton, animations, pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_relpath",
            "image_path": "fixtures/zorder/source.png",
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
        })
        assert res.isError
        text = res.content[0].text
        assert "absolute" in text.lower()
        assert "fixtures/zorder/source.png" in text   # echoes the bad value


async def test_build_rig_reports_missing_image():
    skeleton, animations, pieces = _zorder_inputs()
    missing = str(FIX / "no-such-image.png")
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_noimg",
            "image_path": missing,
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
        })
        assert res.isError
        text = res.content[0].text
        assert "does not exist" in text
        assert missing in text                        # echoes the bad path


def test_build_rig_rejects_bad_weights_algo(tmp_path):
    # weights_algo validation must fire before any build attempt — call the
    # underlying tool function directly (as test_mcp_guide_topics.py does)
    # rather than through the async client session, so the ToolError raises
    # instead of coming back as an isError result.
    from PIL import Image as PILImage
    png = tmp_path / "x.png"
    PILImage.new("RGBA", (8, 8)).save(png)
    with pytest.raises(textrig_server.ToolError, match="weights_algo"):
        textrig_server.build_rig(name="t", image_path=str(png), skeleton=[],
                                 animations={}, weights_algo="warm")


async def test_build_rig_declares_knob_params():
    # The generator knobs must be real declared parameters (FastMCP silently
    # ignores unknown arguments, so a call-only test would pass vacuously).
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        tools = (await client.list_tools()).tools
        build = next(t for t in tools if t.name == "build_rig")
        props = build.inputSchema["properties"]
        for knob in ("fill_blur", "bg_tolerance", "bg", "power", "cols",
                     "fill_mode", "weights_algo"):
            assert knob in props, f"build_rig is missing knob param {knob!r}"


async def test_build_rig_fill_mode_invokes_orchestrator_directly():
    # fill_mode is an orchestrator knob like fill_blur/bg_tolerance/bg — setting
    # it on a pieces build must route around build_rig.sh to build_pieces_rig.py
    # and still succeed.
    skeleton, animations, pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_fillmode",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
            "fill_mode": "extend",
        })
        assert not res.isError, res.content[0].text
        assert (REPO / "out" / "mcptest_fillmode_ske.json").is_file()


async def test_build_rig_fill_mode_rejected_on_wholeimage_build():
    # No pieces document -> no fill_occlusions at all; fill_mode must error
    # loudly instead of being silently ignored (same contract as the other
    # orchestrator-only knobs).
    skeleton, animations, _pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_fillmode_whole",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "fill_mode": "extend",
        })
        assert res.isError
        assert "fill_mode" in res.content[0].text


async def test_build_rig_knobs_invoke_orchestrator_directly():
    # Setting an orchestrator knob on a pieces build routes around
    # build_rig.sh (which doesn't forward knobs) to build_pieces_rig.py —
    # the build must still succeed and produce the triple.
    skeleton, animations, pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("build_rig", {
            "name": "mcptest_knobs",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
            "fill_blur": 1.5,
            "bg_tolerance": 24,
        })
        assert not res.isError, res.content[0].text
        assert (REPO / "out" / "mcptest_knobs_ske.json").is_file()


async def test_render_after_failed_rebuild_reports_no_rig():
    # A failed rebuild must not leave the PREVIOUS build's triple around:
    # build ok, then rebuild the same name with broken markup (fails), then
    # render must refuse with an explicit "has not succeeded" error instead
    # of silently rendering the stale rig.
    skeleton, animations, pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        ok = await client.call_tool("build_rig", {
            "name": "mcptest_stale",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
        })
        assert not ok.isError, ok.content[0].text
        assert (REPO / "out" / "mcptest_stale_ske.json").is_file()

        broken = json.loads((FIX / "animations.json").read_text())
        broken["flip"]["tracks"][0]["keys"][1]["v"] = 99
        bad = await client.call_tool("build_rig", {
            "name": "mcptest_stale",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": broken,
            "pieces": pieces,
        })
        assert bad.isError
        assert not (REPO / "out" / "mcptest_stale_ske.json").is_file()  # guard

        res = await client.call_tool("render", {"name": "mcptest_stale"})
        assert res.isError
        text = res.content[0].text
        assert "has not succeeded" in text
        assert "mcptest_stale" in text


@pytest.mark.skipif(NODE_MISSING, reason="renderer/node_modules missing — run pnpm install there")
async def test_render_returns_image_block():
    skeleton, animations, pieces = _zorder_inputs()
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        build_res = await client.call_tool("build_rig", {
            "name": "mcptest_render",
            "image_path": str(FIX / "source.png"),
            "skeleton": skeleton,
            "animations": animations,
            "pieces": pieces,
        })
        assert not build_res.isError, build_res.content[0].text

        res = await client.call_tool("render", {
            "name": "mcptest_render",
            "animation": "flip",
            "frames": 4,
            "size": 128,
        })
        assert not res.isError, res.content[0].text
        images = [c for c in res.content if c.type == "image"]
        assert len(images) == 1
        assert images[0].mimeType == "image/png"
        assert len(images[0].data) > 0


async def test_render_caps_rejected():
    async with create_connected_server_and_client_session(
            textrig_server.mcp._mcp_server) as client:
        res = await client.call_tool("render", {
            "name": "mcptest_render",
            "frames": 50,
        })
        assert res.isError
        assert "12" in res.content[0].text
