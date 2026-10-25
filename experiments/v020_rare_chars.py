"""v0.2.0 issue 1: which characters of a text are rare. GB2312 level, how many tokens the
Qwen3-TTS tokenizer needs for each (more than one = no embedding of its own), pypinyin readings.

usage: .venv/bin/python experiments/v020_rare_chars.py TTS_MODEL_SNAPSHOT_DIR BOOK.txt
"""
import re, sys, collections
from transformers import AutoTokenizer
from pypinyin import pinyin, Style
TOK = AutoTokenizer.from_pretrained(sys.argv[1])
text = open(sys.argv[2], encoding="utf-8").read()

def gb_level(ch):
    try:
        b = ch.encode("gb2312")
    except UnicodeEncodeError:
        return 0           # not in GB2312
    return 1 if 0xB0 <= b[0] <= 0xD7 else 2

def tokens(ch):
    return len(TOK.encode(ch, add_special_tokens=False))

chars = collections.Counter(c for c in text if '一' <= c <= '鿿')
print("distinct hanzi", len(chars))
lvl = collections.Counter(gb_level(c) for c in chars)
print("GB2312 L1/L2/none:", lvl[1], lvl[2], lvl[0])
multi = [c for c in chars if tokens(c) > 1]
print("multi-token chars:", len(multi))
for c in sorted(set(multi) | {c for c in chars if gb_level(c) != 1}, key=lambda c: -chars[c]):
    ctx = collections.Counter(m.group(0) for m in re.finditer(".{0,2}" + c + ".{0,2}", text)).most_common(3)
    print(c, chars[c], "gb", gb_level(c), "tok", tokens(c), pinyin(c, style=Style.TONE, heteronym=True)[0], [x for x, _ in ctx])
