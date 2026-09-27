# 广告

A flash fiction by 半轻人 (2026): about 1,400 characters of modern Simplified Chinese. An
alien couple, packed for a holiday on Earth, watch Earth's tourism advertisement, and
think better of it.

It is huashuo's second golden example, for the voice library: eleven speakers of every age
but teenagers (two children, young, middle-aged and elderly men and women), one line each,
so casting has to spread them over the library. The chef's and the engineer's genders are
not stated, which exercises the gender inference; the two aliens never speak, so they must
not be cast.

| File | Purpose |
|---|---|
| `txt/广告.txt` | The story: title line, `作者：半轻人`, then the text |
| `speakers.tsv` | The speaker of every quote: the golden labels for speaker attribution |
| `cast.json` | The demo's cast: the machine's, with choices changed by ear (the TV host 小胡子 in `mid_man_jovial` and the chef, who speaks right after him, in `mid_man_steady`; the pilot, in his forties, in `young_man_deep` rather than an old man's voice, and the engineer in `young_man_warm`) |
| `llm-cache/` | The LLM's answers for this story, replayed offline by `tests/test_golden.py`; re-record them with `examples/refresh_golden.py ad` (a capped, paid run) when the prompts change |

    huashuo examples/ad/txt/广告.txt

To make the demo with its cast, and without paying for the LLM again, put the cast and the
stored answers in the work directory before the first import:

    mkdir -p examples/ad/txt/广告.huashuo/state
    cp examples/ad/cast.json examples/ad/txt/广告.huashuo/
    cp -R examples/ad/llm-cache examples/ad/txt/广告.huashuo/state/
    huashuo examples/ad/txt/广告.txt

License: the story is CC BY-NC-ND 4.0, with the author's permission to make and share
audio with huashuo for testing and demonstration; see [LICENSE](LICENSE).
