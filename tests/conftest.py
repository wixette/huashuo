import logging
import os
import shutil
from pathlib import Path

import pytest

from helpers import SAMPLE_TXT


def pytest_collection_modifyitems(config, items):
    """Tests marked `ffmpeg` skip when ffmpeg is missing, unless CI requires it."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    if os.environ.get("HUASHUO_REQUIRE_FFMPEG"):
        raise pytest.UsageError("ffmpeg/ffprobe not found, but HUASHUO_REQUIRE_FFMPEG is set")
    skip = pytest.mark.skip(reason="ffmpeg not installed")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, request):
    """No test reaches a paid API by accident: keys are hidden and pydantic-ai refuses
    model requests, except in tests marked `llm`."""
    if "llm" in request.keywords:
        return
    for name in list(os.environ):
        if name.startswith("HUASHUO_LLM_") or name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            monkeypatch.delenv(name)
    try:
        import pydantic_ai.models
    except ImportError:
        return
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def clean_logging():
    """The CLI attaches a file handler per run; do not let it leak into the next test."""
    yield
    log = logging.getLogger("huashuo")
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "红楼梦.txt"
    path.write_text(SAMPLE_TXT, encoding="utf-8")
    return path
