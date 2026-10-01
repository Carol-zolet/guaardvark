"""Integration tests for the FastAPI app.

Uses httpx TestClient. Model-dependent tests are skipped if no GPU.
"""
import pytest
from fastapi.testclient import TestClient

# Mock torch.cuda before importing app to allow CPU-only test runs
import torch
if not torch.cuda.is_available():
    torch.cuda.is_available = lambda: False

from service.app import app
from service.auth import auth_token

AUTH_HEADER = {"Authorization": f"Bearer {auth_token()}"}


@pytest.fixture(scope="module")
def client():
    """TestClient as context manager triggers lifespan events. The base URL
    is an address the service's Host check answers."""
    with TestClient(app, base_url="http://127.0.0.1:8202") as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "gpu" in data
    # The backend relays /health to browsers; the token stays in its file.
    assert "auth_token" not in data
    assert auth_token() not in resp.text


def test_a_rebound_name_is_refused(client):
    resp = client.get("/health", headers={"Host": "evil.example:8202"})
    assert resp.status_code == 421


def test_models_endpoint(client):
    resp = client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "downloaded" in data
    assert "available" in data


def test_config_endpoint(client):
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "default_model" in data


def test_jobs_endpoint_empty(client):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_upscale_image_requires_auth(client):
    """POST endpoints require bearer token."""
    resp = client.post("/upscale/image", json={"input_path": "/fake", "output_path": "/fake_out"})
    assert resp.status_code == 401


def test_upscale_video_requires_auth(client):
    resp = client.post("/upscale/video", json={"input_path": "/fake"})
    assert resp.status_code == 401


def test_upscale_video_validates_input(client):
    resp = client.post(
        "/upscale/video",
        json={"input_path": "/nonexistent/video.mp4"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 400


def test_job_not_found(client):
    resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404


def test_cancel_job_requires_auth(client):
    resp = client.delete("/jobs/someid")
    assert resp.status_code == 401


def test_upscale_images_requires_auth(client):
    resp = client.post(
        "/upscale/images",
        json={"inputs": ["/fake.png"], "output_dir": "/tmp/out"},
    )
    assert resp.status_code == 401


def test_upscale_images_rejects_empty_input_list(client):
    resp = client.post(
        "/upscale/images",
        json={"inputs": [], "output_dir": "/tmp/out"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 400


def test_upscale_images_validates_inputs_exist(client):
    resp = client.post(
        "/upscale/images",
        json={"inputs": ["/nonexistent/frame.png"], "output_dir": "/tmp/out"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 400


def test_atomic_imwrite_keeps_the_target_extension(tmp_path):
    """OpenCV picks its encoder off the final extension, so the temp file keeps it."""
    import numpy as np
    from service.app import _atomic_imwrite

    target = tmp_path / "still_upscaled.png"
    assert _atomic_imwrite(str(target), np.zeros((4, 4, 3), np.uint8)) is True
    assert target.read_bytes().startswith(b"\x89PNG")
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_imwrite_creates_missing_directories(tmp_path):
    import numpy as np
    from service.app import _atomic_imwrite

    target = tmp_path / "nested" / "out.png"
    assert _atomic_imwrite(str(target), np.zeros((4, 4, 3), np.uint8)) is True
    assert target.exists()

