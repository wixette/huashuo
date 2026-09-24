"""Split paragraphs into narration and quoted pieces (TXT-6, SCR-2).

Deterministic and model-free: every quoted span becomes a `dialogue` block (speaker
"unknown" until attribution runs), the text around it stays `narration`. Attribution may
later turn a quote that is not speech (a title, a quoted phrase) back into narration.

Pieces are exact sub-slices of the paragraph, so their `src` offsets keep invariants I4
and I5 (docs/script-ir.md §5). Ids extend the paragraph's: c001.p0012 -> c001.p0012.01,
.02, … (§3.3).
"""

from __future__ import annotations

# Opening quote -> its closing quote. The straight double quote closes itself.
_PAIRS = {
    "zh": {"“": "”", "「": "」", "『": "』", '"': '"'},
    "en": {"“": "”", '"': '"'},
}


def quote_spans(text: str, language: str) -> list[tuple[int, int]]:
    """Top-level quoted spans, quotes included, as (start, end) offsets.

    Nested quotes (『』 inside 「」, “ ” inside 「」) stay inside their outer span. A quote
    still open at the end of the paragraph runs to the end: long speeches often continue
    into the next paragraph, which then opens with a fresh quote mark.
    """
    pairs = _PAIRS.get(language, _PAIRS["zh"])
    spans: list[tuple[int, int]] = []
    start = None
    stack: list[str] = []            # closers we are waiting for, innermost last
    for i, ch in enumerate(text):
        if stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                spans.append((start, i + 1))
                start = None
        elif ch in pairs and not (ch == '"' and stack and stack[-1] == '"'):
            if not stack:
                start = i
            stack.append(pairs[ch])
    if stack and start is not None and len(text) - start > 1:
        spans.append((start, len(text)))
    return spans


def split_block(block: dict, language: str) -> list[dict]:
    """The pieces of one narration block, or [block] if it has no quotes."""
    text = block.get("text", "")
    spans = quote_spans(text, language)
    if not spans:
        return [block]
    if spans == [(0, len(text))]:
        # The whole paragraph is one quote: it keeps its id and becomes dialogue.
        return [{**block, "type": "dialogue", "speaker": "unknown"}]
    pieces: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end in spans:
        if text[cursor:start].strip():
            pieces.append((cursor, start, "narration"))
        pieces.append((start, end, "dialogue"))
        cursor = end
    if text[cursor:].strip():
        pieces.append((cursor, len(text), "narration"))

    base = block["src"][0] if isinstance(block.get("src"), list) else None
    out = []
    for n, (start, end, kind) in enumerate(pieces, 1):
        # Trim surrounding whitespace so each piece's text is exactly what it covers.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        piece = {"id": f"{block['id']}.{n:02d}", "type": kind, "text": text[start:end]}
        if kind == "dialogue":
            piece["speaker"] = "unknown"
        if base is not None:
            piece["src"] = [base + start, base + end]
        out.append(piece)
    if block.get("pause_after") is not None:
        out[-1]["pause_after"] = block["pause_after"]
    return out


def split_blocks(blocks: list[dict], language: str) -> list[dict]:
    """Every narration block split at its quotes; all other blocks unchanged."""
    out: list[dict] = []
    for block in blocks:
        if block.get("type") == "narration":
            out.extend(split_block(block, language))
        else:
            out.append(block)
    return out
