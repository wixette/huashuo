import ipaddress
import logging
import os
import shutil
import socket
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


# --------------------------------------------------------------------------------------
# Policy: no test may reach a paid API or the network. Enforced three ways, for every
# test without exception: API keys are removed from the environment and .env loading is
# switched off; pydantic-ai refuses real model requests; and any socket connection to a
# non-loopback address fails. LLM behaviour is tested with pydantic-ai's TestModel and
# FunctionModel. Real-LLM accuracy checks are manual scripts with a --max-cost cap, never
# part of this suite.
# --------------------------------------------------------------------------------------

_SECRET_ENV = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
               "DASHSCOPE_API_KEY", "ANTHROPIC_API_KEY")


class NetworkBlocked(ConnectionRefusedError):
    """An OSError, so libraries treat it as a failed connection and close their socket."""


def _is_local(address) -> bool:
    if isinstance(address, (str, bytes)):          # AF_UNIX socket path
        return True
    host = address[0] if isinstance(address, tuple) and address else ""
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    for name in list(os.environ):
        if name.startswith("HUASHUO_LLM_") or name in _SECRET_ENV:
            monkeypatch.delenv(name)
    monkeypatch.setenv("HUASHUO_NO_DOTENV", "1")
    try:
        import pydantic_ai.models
        monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)
    except ImportError:
        pass

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded(real):
        def connect(self, address):
            if not _is_local(address):
                raise NetworkBlocked(f"tests may not open network connections (tried {address!r})")
            return real(self, address)
        return connect

    monkeypatch.setattr(socket.socket, "connect", guarded(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(real_connect_ex))


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


@pytest.fixture
def tiny_library(tmp_path, monkeypatch):
    """A small voice library in place of the packaged one: four presets (one a dialect)
    and two library voices with random vectors. Returns its root directory."""
    import json

    import numpy as np

    import huashuo.library as library

    root = tmp_path / "voices"
    (root / "zh").mkdir(parents=True)
    (root / "presets.json").write_text(json.dumps({
        "serena": {"language": "zh", "gender": "female", "age": "young_adult", "role": "预置女声", "traits": ["narrator"]},
        "vivian": {"language": "zh", "gender": "female", "age": "young_adult", "role": "预置女声二"},
        "uncle_fu": {"language": "zh", "gender": "male", "age": "middle_aged", "role": "预置中年男声"},
        "dylan": {"language": "zh", "gender": "male", "age": "young_adult", "role": "北京话", "traits": ["dialect"]},
    }, ensure_ascii=False), encoding="utf-8")
    rng = np.random.default_rng(0)
    for vid, gender, age in (("young_man", "male", "young_adult"), ("old_man", "male", "elderly")):
        (root / "zh" / f"{vid}.json").write_text(json.dumps({"gender": gender, "age": age, "role": vid}),
                                                 encoding="utf-8")
        np.save(root / "zh" / f"{vid}.npy", rng.standard_normal(2048).astype(np.float32))
    library._load.cache_clear()
    monkeypatch.setattr(library, "ROOT", root)
    yield root
    library._load.cache_clear()
