"""Keep model-loading chatter off the terminal.

Loading a model prints several lines every run: huggingface_hub's "Fetching 12 files" bar,
hf_xet's "Reconstruction complete" / "Download complete" (native code, written straight to
the terminal), mlx-audio's "Initialized encoder codebooks" and "Loaded speech tokenizer
from ...". While a model that is already downloaded loads, stdout and stderr are sent to a
temporary file at the file-descriptor level, which is the only way to catch the native
output. What was captured goes to the log; if loading fails it is shown, so no error is
hidden. A first download is left alone: its progress is worth seeing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import tempfile

log = logging.getLogger("huashuo")


def downloaded(repo_id: str) -> bool:
    """The model is in the local Hugging Face cache (loading it downloads nothing)."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return isinstance(try_to_load_from_cache(repo_id, "config.json"), str)


@contextlib.contextmanager
def quiet_loading(repo_id: str):
    if not downloaded(repo_id):
        yield
        return
    fds = [1, 2]                  # the process's own: native code writes there, whatever sys.stdout is
    try:
        for fd in fds:
            os.fstat(fd)
    except OSError:
        yield
        return
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(fd) for fd in fds]
    with tempfile.TemporaryFile() as sink:
        for fd in fds:
            os.dup2(sink.fileno(), fd)
        # Python's sys.stdout is not always fd 1 (under pytest it is not): redirect it too.
        stream = open(sink.fileno(), "w", encoding="utf-8", closefd=False)
        failed = False
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                yield
        except BaseException:
            failed = True
            raise
        finally:
            stream.close()                                  # the sink's fd stays open (closefd=False)
            sys.stdout.flush()
            sys.stderr.flush()
            for fd, copy in zip(fds, saved):
                os.dup2(copy, fd)
                os.close(copy)
            sink.seek(0)
            text = sink.read().decode("utf-8", errors="replace")
            captured = "\n".join(line for line in text.splitlines() if line.strip())
            if captured:
                log.info("loading %s:\n%s", repo_id, captured)
                if failed:
                    print(captured, file=sys.stderr)
