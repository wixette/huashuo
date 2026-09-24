"""Encode the programme into an M4B with chapters, metadata and cover (M4B-1 … M4B-4).

ffmpeg does the encoding, called as a subprocess with list arguments (NFR-7, NFR-8). On
macOS the AudioToolbox AAC encoder (`aac_at`) is used when present; it is noticeably
better than ffmpeg's built-in AAC at the 64 kbps an audiobook needs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_BITRATE = "64k"
LANGUAGE_CODES = {"zh": "chi", "en": "eng"}   # ISO 639-2 for the audio stream


class PackageError(Exception):
    pass


@dataclass
class BookInfo:
    title: str
    author: str
    language: str
    narrator: str = "话说 Huashuo（Qwen3-TTS）"
    description: str = ""
    date: str = ""


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise PackageError(f"{name} not found on PATH; install it with `brew install ffmpeg`")
    return path


def aac_encoder() -> str:
    out = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    return "aac_at" if " aac_at " in out else "aac"


def _escape(value: str) -> str:
    """ffmetadata escaping: '=', ';', '#', '\\' and newlines are backslash-escaped."""
    for ch in "\\=;#\n":
        value = value.replace(ch, "\\" + ch)
    return value


def ffmetadata(info: BookInfo, chapters: list[tuple[str, int, int]], sample_rate: int) -> str:
    tags = {"title": info.title, "album": info.title, "artist": info.author,
            "album_artist": info.author,
            # No standard narrator atom exists; players and Audiobookshelf read it from composer.
            "composer": info.narrator, "genre": "Audiobook", "date": info.date,
            "comment": info.description, "description": info.description}
    lines = [";FFMETADATA1"] + [f"{k}={_escape(v)}" for k, v in tags.items() if v]
    for title, start, end in chapters:
        lines += ["", "[CHAPTER]", f"TIMEBASE=1/{sample_rate}", f"START={start}", f"END={end}",
                  f"title={_escape(title)}"]
    return "\n".join(lines) + "\n"


def write_m4b(output: Path, pcm: Iterable[bytes], sample_rate: int, info: BookInfo,
              chapters: list[tuple[str, int, int]], cover: Path | None,
              bitrate: str = DEFAULT_BITRATE) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_name(f"{output.name}.{os.getpid()}.tmp.m4b")
    with tempfile.TemporaryDirectory() as tmp:
        meta_path = Path(tmp) / "metadata.txt"
        meta_path.write_text(ffmetadata(info, chapters, sample_rate), encoding="utf-8")
        command = [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
                   "-i", str(meta_path)]
        if cover is not None:
            command += ["-i", str(cover)]
        command += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
                    "-c:a", aac_encoder(), "-b:a", bitrate,
                    "-metadata:s:a:0", f"language={LANGUAGE_CODES.get(info.language, 'und')}"]
        if cover is not None:
            command += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        # Major brand "M4B " marks the file as an audiobook for players that look (M4B-5).
        command += ["-movflags", "+faststart", "-brand", "M4B ", "-f", "ipod", str(tmp_output)]

        # ffmpeg's messages go to a file: an unread pipe could fill up and stall both sides.
        with open(Path(tmp) / "ffmpeg.log", "w+b") as log_file:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log_file)
            try:
                for chunk in pcm:
                    process.stdin.write(chunk)
                process.stdin.close()
            except BrokenPipeError:
                pass
            except BaseException:
                process.kill()
                process.wait()
                tmp_output.unlink(missing_ok=True)
                raise
            code = process.wait()
            log_file.seek(0)
            messages = log_file.read().decode("utf-8", errors="replace")
        if code != 0:
            tmp_output.unlink(missing_ok=True)
            raise PackageError(f"ffmpeg failed ({code}): {messages.strip()[-800:]}")
    os.replace(tmp_output, output)


def probe(path: Path) -> dict:
    """Duration, chapters and tags as ffprobe sees them, for verification and tests."""
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_format", "-show_chapters",
                          "-show_streams", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# --------------------------------------------------------------------------------------
# A plain text cover for books that have none (M4B-4)
# --------------------------------------------------------------------------------------

_FONTS = ["/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
          "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc",
          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]


def make_cover(path: Path, title: str, author: str, size: int = 1400) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((f for f in _FONTS if Path(f).is_file()), None)

    def font(px: int):
        return ImageFont.truetype(font_path, px) if font_path else ImageFont.load_default(size=px)

    image = Image.new("RGB", (size, size), (38, 42, 48))
    draw = ImageDraw.Draw(image)
    draw.rectangle([60, 60, size - 60, size - 60], outline=(196, 160, 98), width=6)

    def wrap(text: str, fnt, width: int) -> list[str]:
        lines, current = [], ""
        for ch in text:
            if draw.textlength(current + ch, font=fnt) > width and current:
                lines.append(current)
                current = ch
            else:
                current += ch
        return lines + ([current] if current else [])

    title_font = font(130 if len(title) <= 8 else 96)
    lines = wrap(title, title_font, size - 280)
    line_height = title_font.size * 1.3
    y = size * 0.42 - line_height * len(lines) / 2
    for line in lines:
        draw.text((size / 2, y), line, font=title_font, fill=(240, 234, 220), anchor="mt")
        y += line_height
    if author:
        draw.text((size / 2, y + 60), author, font=font(64), fill=(196, 160, 98), anchor="mt")
    draw.text((size / 2, size - 150), "话说 Huashuo", font=font(44), fill=(150, 150, 150), anchor="mt")
    image.save(path, "JPEG", quality=90)
    return path
