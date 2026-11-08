import json
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image
from test_cover_poster_planner import response
from test_poster_studio_api import (
    generate,
    overview,
    select,
    studio,  # noqa: F401
    wait_task,
)

from api import poster_studio
from core.reference_assets import ReferenceAssets


def png():
    out = BytesIO()
    Image.new("RGB", (480, 640), "salmon").save(out, format="PNG")
    return out.getvalue()


def test_reference_full_bytes_reach_one_planning_call_and_four_image_edits(
    studio, monkeypatch
):
    raw = png()
    uploaded = studio.client.post(
        studio.base + "/reference", files={"file": ("cover.png", raw, "image/png")}
    )
    assert uploaded.status_code in (200, 201), uploaded.text
    value = uploaded.json()
    reference = value.get("reference", value)
    assert overview(studio)["reference"]["asset"]["sha256"] == sha256(raw).hexdigest()
    calls = []

    class Vision:
        model = "vision-fixture"
        provider_name = "fixture"

        def complete_with_images(self, system, user, images):
            calls.append((system, json.loads(user), images))
            out = response()
            out["reference_analysis"] = {
                k: ["Visible figures and light."]
                for k in (
                    "observations",
                    "interpretations",
                    "retain",
                    "translate",
                    "exclude",
                )
            }
            return json.dumps(out)

    monkeypatch.setattr(poster_studio, "get_vision_reviewer", lambda: Vision())
    request = studio.client.post(
        studio.base + "/plans",
        json={
            "request_id": "reference-plan-001",
            "preferences": {},
            "reference": {
                "asset_id": reference["asset_id"],
                "mode": "adaptation_reference",
            },
        },
    )
    assert request.status_code == 202, request.text
    task = wait_task(studio, request.json()["task_id"])
    assert task["status"] == "complete", task
    plan = next(
        p
        for p in overview(studio)["plans"]
        if p["plan_id"] == task["result"]["plan_id"]
    )
    assert len(calls) == 1 and calls[0][2] == [raw]
    assert plan["reference"]["asset"]["sha256"] == sha256(raw).hexdigest()
    assert plan["reference_analysis"]["observations"]
    task, candidates = generate(studio, plan)
    assert task["status"] == "complete", task
    assert len(studio.images.calls) == 4 and all(
        c[0] == "edit" for c in studio.images.calls
    )
    assert select(studio, candidates, candidates["candidates"][0]).status_code == 200


def test_reference_mime_validation_and_removal_are_not_model_calls(studio):
    bad = studio.client.post(
        studio.base + "/reference",
        files={"file": ("fake.png", b"not an image", "image/png")},
    )
    assert bad.status_code == 422
    uploaded = studio.client.post(
        studio.base + "/reference", files={"file": ("x.jpg", png(), "image/jpeg")}
    )
    assert uploaded.status_code in (200, 201)
    assert overview(studio)["reference"]["asset"]["content_type"] == "image/png"
    cleared = studio.client.post(
        studio.base + "/reference/selection",
        json={"asset_id": None, "mode": "visual_inspiration"},
    )
    assert cleared.status_code == 200 and overview(studio)["reference"] is None
    assert not studio.planner.calls and not studio.images.calls


def test_referenced_image_changed_on_disk_is_not_silently_ignored(tmp_path):
    store = ReferenceAssets(tmp_path)
    asset = store.put(png())
    path = store.path(asset["asset_id"])
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        store.read(asset["asset_id"])
