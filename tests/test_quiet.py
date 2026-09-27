"""Model-loading chatter stays off the terminal (huggingface_hub bars, hf_xet, mlx-audio)."""

import logging
import os

import pytest

from huashuo import quiet


def _chatter():
    print("Fetching 12 files: 100%")                      # Python
    os.write(2, b"Download complete\n")                     # native code writes to the fd itself


def test_loading_a_downloaded_model_is_quiet_and_logged(monkeypatch, capfd, caplog):
    monkeypatch.setattr(quiet, "downloaded", lambda repo: True)
    with caplog.at_level(logging.INFO, logger="huashuo"), quiet.quiet_loading("some/model"):
        _chatter()
    out, err = capfd.readouterr()
    assert "Fetching" not in out and "Download" not in err
    assert "Fetching 12 files" in caplog.text and "Download complete" in caplog.text


def test_a_failed_load_shows_what_it_printed(monkeypatch, capfd):
    monkeypatch.setattr(quiet, "downloaded", lambda repo: True)
    with pytest.raises(RuntimeError), quiet.quiet_loading("some/model"):
        _chatter()
        raise RuntimeError("no such model")
    assert "Download complete" in capfd.readouterr().err


def test_a_first_download_shows_its_progress(monkeypatch, capfd):
    monkeypatch.setattr(quiet, "downloaded", lambda repo: False)
    with quiet.quiet_loading("some/model"):
        _chatter()
    out, err = capfd.readouterr()
    assert "Fetching" in out and "Download complete" in err
