"""Guards that run locally and in CI: no secrets in the repository, and the test suite
really cannot reach paid APIs or the network."""

import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Common API-key shapes: OpenAI (sk-, sk-proj-), Anthropic (sk-ant-), GitHub, AWS, Google.
KEY_PATTERNS = re.compile(
    r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{35}")


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


needs_git = pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                               reason="not a git checkout")


@needs_git
def test_no_api_keys_in_tracked_files():
    offenders = []
    for name in _git("ls-files", "-z").split("\0"):
        path = ROOT / name
        if not name or not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if KEY_PATTERNS.search(text):
            offenders.append(name)
    assert not offenders, f"possible API keys committed in: {offenders}"


@needs_git
def test_env_files_are_ignored():
    for name in (".env", "experiments/.env"):
        assert _git("check-ignore", name).strip(), f"{name} must be git-ignored"
    assert not [n for n in _git("ls-files").splitlines() if Path(n).name == ".env"]


def test_network_is_blocked_in_tests():
    with pytest.raises(OSError, match="may not open network"):
        socket.create_connection(("api.openai.com", 443), timeout=1)


@pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning")
def test_llm_requests_are_refused_and_keys_hidden(monkeypatch):
    import os

    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    assert not any(k.startswith("HUASHUO_LLM_") or k == "OPENAI_API_KEY" for k in os.environ)
    assert os.environ.get("HUASHUO_NO_DOTENV") == "1"
    agent = Agent(OpenAIChatModel("gpt-6-sol", provider=OpenAIProvider(api_key="sk-test-not-a-real-key")))
    with pytest.raises(RuntimeError, match="(?i)model requests|not allowed"):
        agent.run_sync("hello")
