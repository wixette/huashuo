# 一条被洗澡水拍死的鱼

A short story by 半轻人, first published on 2016-04-01 at
<https://ygwang.info/fictions/fish/>: about 9,500 characters of modern Simplified
Chinese, in nine numbered sections, told in the first person by a photographer, with a
woman, 栖芒, as the other main voice. It is huashuo's example and golden-test input.

| File | Purpose |
|---|---|
| `txt/一条被洗澡水拍死的鱼.txt` | Plain text: title line, `作者：半轻人`, then the story |
| `epub/一条被洗澡水拍死的鱼.epub` | The same text as EPUB 3 (one chapter per section, cover from huashuo's templates), built by `examples/make_epub.py` |
| `speakers.tsv` | The speaker of every quote, checked by the author: the golden labels for speaker attribution |
| `llm-cache/` | The LLM's answers for this story, replayed offline by `tests/test_golden.py`; re-record them with `examples/refresh_golden.py` (a capped, paid run) when the prompts change |

The two formats sit in separate folders because a book's work directory is named after
the file without its extension.

    huashuo examples/fish/txt/一条被洗澡水拍死的鱼.txt
    huashuo examples/fish/epub/一条被洗澡水拍死的鱼.epub

To make the demo audiobook (attached to huashuo's releases) without paying for the LLM again, put the stored answers in the work
directory before the first import:

    mkdir -p examples/fish/epub/一条被洗澡水拍死的鱼.huashuo/state
    cp -R examples/fish/llm-cache examples/fish/epub/一条被洗澡水拍死的鱼.huashuo/state/
    huashuo examples/fish/epub/一条被洗澡水拍死的鱼.epub

Rebuild the EPUB after editing the text:

    .venv/bin/python examples/make_epub.py examples/fish/txt/一条被洗澡水拍死的鱼.txt \
        --date 2016-04-01 --source https://ygwang.info/fictions/fish/ --rights "CC BY-NC-ND 4.0"
    mv examples/fish/txt/一条被洗澡水拍死的鱼.epub examples/fish/epub/

License: the story is CC BY-NC-ND 4.0, with the author's permission to make and share
audio with huashuo for testing and demonstration; see [LICENSE](LICENSE).
