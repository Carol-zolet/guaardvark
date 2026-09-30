"""Photo edits render at the editing model's registry step counts and say what ran.

FLUX.1 Kontext declares its floor and default on its registry entry; an edit,
inpaint or outpaint that names no count renders the default, a lower count is
raised to the floor with a sentence in the result, and a count marked as typed
by a person stands. ComfyUI is a stand-in: no GPU, network or database.
"""
from __future__ import annotations

import contextlib

import pytest

from backend import config
from backend.services import comfyui_image_generator as cig
from backend.services import video_model_registry as vmr
from backend.tools import image_tools as it


@pytest.fixture
def comfy(monkeypatch, tmp_path):
    """ComfyUIImageGenerator with the network calls replaced; returns the queued workflows."""
    queued = []
    gen = cig.ComfyUIImageGenerator
    monkeypatch.setattr(gen, "_kontext_installed", lambda self: True)
    monkeypatch.setattr(gen, "qwen_edit_installed", lambda self: False)
    monkeypatch.setattr(gen, "_require_up", lambda self, message: None)
    monkeypatch.setattr(gen, "_edit_gpu_session",
                        staticmethod(lambda op_id, gpu_wait, **kw: contextlib.nullcontext()))
    monkeypatch.setattr(gen, "_upload_image_to_comfyui", lambda self, path: "src.png")
    monkeypatch.setattr(gen, "_queue", lambda self, workflow: queued.append(workflow) or "prompt-1")
    monkeypatch.setattr(gen, "_wait", lambda self, prompt_id, timeout=None: {})
    monkeypatch.setattr(gen, "_fetch_first_image", lambda self, outputs, output_path: output_path)
    monkeypatch.setattr(gen, "_run_edit_graph",
                        lambda self, workflow, output_path, **kw: queued.append(workflow) or output_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(it, "_chat_gpu_wait", lambda: None)
    return queued


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"png")
    return str(path)


def _sampler_steps(workflow: dict) -> int:
    return next(n["inputs"]["steps"] for n in workflow.values() if n.get("class_type") == "KSampler")


def test_the_kontext_floor_is_declared_on_its_registry_entry():
    entry = vmr.VIDEO_MODEL_REGISTRY["flux-kontext-dev"]
    assert entry["min_steps"] == cig.KONTEXT_MIN_STEPS
    assert entry["default_steps"] == cig.KONTEXT_DEFAULT_STEPS
    assert entry["default_steps"] >= entry["min_steps"]
    assert not [p for p in vmr.verify_registry() if p.startswith("flux-kontext-dev:")]


def test_edit_steps_defaults_floors_and_keeps_a_typed_count():
    rule = dict(floor=28, default=28, label="FLUX.1 Kontext")
    assert cig.edit_steps(None, **rule) == (28, None)
    assert cig.edit_steps(0, **rule) == (28, None)
    assert cig.edit_steps("junk", **rule) == (28, None)
    assert cig.edit_steps(40, **rule) == (40, None)
    steps, notice = cig.edit_steps(20, **rule)
    assert steps == 28 and "raised 20 to 28" in notice
    steps, notice = cig.edit_steps(12, explicit=True, **rule)
    assert steps == 12 and "kept the 12" in notice and "28" in notice
    # A default below the floor never renders.
    assert cig.edit_steps(None, floor=28, default=20, label="x") == (28, None)


def test_a_kontext_edit_queues_the_floor_not_the_count_below_it(comfy, photo, tmp_path):
    floor = cig.KONTEXT_MIN_STEPS
    for asked, explicit, rendered in ((None, False, cig.KONTEXT_DEFAULT_STEPS), (floor - 8, False, floor),
                                      (4, False, floor), (floor + 12, False, floor + 12), (12, True, 12)):
        gen = cig.ComfyUIImageGenerator()
        gen.edit_image(image_path=photo, instruction="x", output_path=str(tmp_path / "o.png"),
                       steps=asked, steps_explicit=explicit)
        assert _sampler_steps(comfy[-1]) == rendered == gen.last_steps
        changed_or_below = asked is not None and asked < floor
        assert bool(gen.last_steps_notice) == changed_or_below


def test_inpaint_and_outpaint_render_kontext_at_its_default_when_no_count_is_given(comfy, photo):
    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == cig.KONTEXT_DEFAULT_STEPS
    assert res.metadata["steps"] == cig.KONTEXT_DEFAULT_STEPS and res.metadata["steps_notice"] is None
    assert f"Steps: {cig.KONTEXT_DEFAULT_STEPS}" in res.output and "raised" not in res.output

    res = it.OutpaintImageTool().execute(image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == cig.KONTEXT_DEFAULT_STEPS
    assert res.metadata["steps"] == cig.KONTEXT_DEFAULT_STEPS


def test_a_low_count_from_a_tool_call_is_raised_and_the_result_says_so(comfy, photo):
    floor = cig.KONTEXT_MIN_STEPS
    res = it.EditImageTool().execute(instruction="make it night", image=photo, steps=4)
    assert res.success and _sampler_steps(comfy[-1]) == floor
    assert f"raised 4 to {floor}" in res.output and res.metadata["steps"] == floor
    assert f"raised 4 to {floor}" in res.metadata["steps_notice"]

    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo, steps=floor - 8)
    assert _sampler_steps(comfy[-1]) == floor and f"raised {floor - 8} to {floor}" in res.output


def test_qwen_keeps_its_own_floor_and_reports_it(comfy, photo, monkeypatch, tmp_path):
    monkeypatch.setattr(cig.ComfyUIImageGenerator, "qwen_edit_installed", lambda self: True)
    floor = cig.QWEN_EDIT_MIN_STEPS
    gen = cig.ComfyUIImageGenerator()
    gen.edit_image_qwen(image_paths=[photo], instruction="x", output_path=str(tmp_path / "q.png"), steps=floor - 10)
    assert _sampler_steps(comfy[-1]) == floor and f"raised {floor - 10} to {floor}" in gen.last_steps_notice

    # inpaint names no count: Qwen renders its floor. edit_image publishes 28, which stands.
    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == floor and res.metadata["backend"] == "qwen"
    res = it.EditImageTool().execute(instruction="make it night", image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == 28
