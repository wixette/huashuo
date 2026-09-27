"""Shared test data and builders (imported by tests; fixtures live in conftest.py)."""

import zipfile
from pathlib import Path

SAMPLE_TXT = """红楼梦
作者：曹雪芹

第一回 甄士隐梦幻识通灵
此开卷第一回也。作者自云：因曾历过一番梦幻之后，故将真事隐去。
　　列位看官：你道此书从何而来？说起根由虽近荒唐，细按则深有趣味。

＊＊＊

第二回 贾夫人仙逝扬州城
诗云：一局输赢料不真，香销茶尽尚逡巡。
他第一章都没看完，就睡着了。
"""


def make_epub(path: Path, cover: bytes | None = b"\xff\xd8fakejpeg") -> Path:
    """A small EPUB 3: nav TOC, one file holding two chapters split by fragment, a
    copyright page, ruby annotations and a cover image."""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试之书</dc:title><dc:creator>无名氏</dc:creator><dc:language>zh-CN</dc:language>
    <dc:publisher>示例出版社</dc:publisher><dc:description>&lt;p&gt;一本书。&lt;/p&gt;</dc:description>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="copy" href="text/copyright.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch" href="text/chapters.xhtml" media-type="application/xhtml+xml"/>
    {'<item id="cover" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>' if cover else ''}
  </manifest>
  <spine><itemref idref="copy"/><itemref idref="ch"/></spine>
</package>"""
    nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><ol>
  <li><a href="text/copyright.xhtml">版权信息</a></li>
  <li><a href="text/chapters.xhtml#c1">第一章 起风</a></li>
  <li><a href="text/chapters.xhtml#c2">第二章 落雨</a></li>
</ol></nav></body></html>"""
    copyright_page = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>版权所有，侵权必究。</p><p>ISBN 000-0-00-000000-0</p></body></html>"""
    chapters = """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ignored</title></head><body>
<h1 id="c1">第一章 起风</h1>
<p>风从北边来，<ruby>吹<rt>chuī</rt></ruby>过山岗。</p>
<p>他推开门&#160;走了出去。</p>
<h1 id="c2">第二章 落雨</h1>
<p>雨下了一整夜。</p>
</body></html>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/text/copyright.xhtml", copyright_page)
        z.writestr("OEBPS/text/chapters.xhtml", chapters)
        if cover:
            z.writestr("OEBPS/images/cover.jpg", cover)
    return path


# A web-novel-style TXT (acceptance corpus stand-in, requirements §5.1): GBK when written,
# volumes, chapters without blank lines, half-width punctuation mixed into Chinese,
# full-width digits, and the noise lines web novels carry (for TXT-5).
WEBNOVEL_TXT = """剑来长安
作者:青衫客

第一卷 风起青萍
第一章 雨夜来客
　　雨下了一整夜,长安城的青石板路泛着冷光.
　　"店家,来一壶酒!"门口站着一个浑身湿透的少年.
　　掌柜抬起头:"客官从哪里来?"
　　少年笑了笑:"从很远的地方来...你别问了."
　　求月票!求推荐票!
第二章 旧剑
　　少年把剑放在桌上,剑身上刻着１２３４个小字.
　　本章完
第二卷 云涌
第三章 出城
　　天亮以后,他背着剑出了城--再也没有回头.
　　(本章未完,请翻页)
　　www.example-novel.com 最新章节免费阅读
"""


# A first-person story (SCR-9). The cast is what gpt-6-sol answered for the full mock story
# (M5): the narrator is named once another character calls him by name.
FIRST_PERSON_TXT = """归乡

第一章 车站
我下火车的时候，天已经黑了。
“林默！这边！”有人在出站口喊我。
我抬头一看，是表哥周强。
“你怎么来了？”我问。
“你妈不放心，非让我来接。”他接过我的箱子。
"""
FIRST_PERSON_CAST = [
    {"op": "insert", "name": "林默", "aliases": ["我", "默儿"], "gender": "male", "age": "young_adult",
     "description": "第一人称叙述者，周强的表亲，休假返乡探望病重的父亲。"},
    {"op": "insert", "name": "周强", "aliases": ["表哥"], "gender": "male", "age": "young_adult",
     "description": "林默的表哥，深夜到车站接林默回家。"}]
FIRST_PERSON_ANSWERS = {"“林默！这边！”": "周强", "“你怎么来了？”": "林默", "“你妈不放心，非让我来接。”": "周强"}


# --------------------------------------------------------------------------------------
# A scripted stand-in for the LLM (pydantic-ai FunctionModel); no network, no cost.
# --------------------------------------------------------------------------------------

DIALOGUE_TXT = """客栈

第一章 风雪
雪下了整整一夜。林渊推开客栈的木门。
“店家，来一壶热酒。”他说。
老者抬起头：“客官面生得很，是从北边来的？”
“从哪儿来不重要。”林渊坐下。
墙上挂着一块匾，写着“宾至如归”。
"""

# Quote text -> speaker, as a perfect model would answer.
DIALOGUE_ANSWERS = {"“店家，来一壶热酒。”": "林渊", "“客官面生得很，是从北边来的？”": "老者",
                    "“从哪儿来不重要。”": "林渊", "“宾至如归”": "narrator"}
DIALOGUE_CAST = [{"op": "insert", "name": "林渊", "gender": "male", "age": "young_adult", "description": "剑客"},
                 {"op": "insert", "name": "老者", "aliases": ["店家"], "gender": "male", "age": "elderly",
                  "description": "客栈掌柜"}]


class ScriptedLLM:
    """Answers the cast pass with a fixed cast and the speaker pass from an answer key,
    reading the requested indices out of the prompt like a real model would."""

    def __init__(self, cast_ops=None, answers=None, drop_first: int | None = None, confidence: float = 0.95,
                 tokens_per_call: int | None = None, voice_choices: dict | None = None,
                 emotions: dict | None = None, genders: dict | None = None):
        self.cast_ops = DIALOGUE_CAST if cast_ops is None else cast_ops
        self.answers = DIALOGUE_ANSWERS if answers is None else answers
        self.drop_first = drop_first      # leave out this many answers on the first speaker call
        self.confidence = confidence
        self.tokens_per_call = tokens_per_call   # report this usage, so budgets behave as with a real model
        self.voice_choices = voice_choices or {}   # name -> voice ref, for the casting call
        self.emotions = emotions or {}             # quote text -> emotion label
        self.genders = genders or {}               # name -> gender, for the gender call
        self.calls = {"cast": 0, "speakers": 0, "voices": 0}
        self.prompts: list[str] = []

    def model(self):
        from pydantic_ai.models.function import FunctionModel
        return FunctionModel(self)

    def __call__(self, messages, info):
        import json
        import re

        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

        prompt = "".join(getattr(part, "content", "") for m in messages for part in getattr(m, "parts", [])
                         if isinstance(getattr(part, "content", None), str))
        self.prompts.append(prompt)
        asked = re.search(r"(?:需要回答的编号：|Answer for indices: )([\d, ]+)", prompt)
        if "需要推断性别的角色" in prompt or "Characters whose gender is not stated" in prompt:
            self.calls["genders"] = self.calls.get("genders", 0) + 1
            asked_names = prompt.split("需要推断性别的角色")[-1].split("Characters whose gender is not stated")[-1]
            payload = {"genders": [{"name": n, "gender": g} for n, g in self.genders.items() if f"- {n}" in asked_names]}
        elif "可用的音色" in prompt or "Available voices" in prompt:   # casting suggestions
            self.calls["voices"] += 1
            payload = {"choices": [{"name": n, "voice": v} for n, v in self.voice_choices.items()]}
        elif asked is None:                                 # the cast pass
            self.calls["cast"] += 1
            payload = {"operations": self.cast_ops if self.calls["cast"] == 1 else []}
        else:
            self.calls["speakers"] += 1
            wanted = [int(n) for n in asked.group(1).split(",")]
            lines = dict(re.findall(r"^\[(\d+)\] [^：:]+[：:](.*)$", prompt, re.M))
            answers = [{"index": i, "speaker": self.answers.get(lines[str(i)].strip(), "unknown"),
                        "confidence": self.confidence} for i in wanted]
            for a in answers:                               # ignored by the schema without emotions
                a["emotion"] = self.emotions.get(lines[str(a["index"])].strip(), "")
            if self.calls["speakers"] == 1 and self.drop_first:
                answers = answers[self.drop_first:]
            payload = {"answers": answers}
        extra = {}
        if self.tokens_per_call:
            from pydantic_ai.usage import RequestUsage
            extra["usage"] = RequestUsage(input_tokens=self.tokens_per_call, output_tokens=0)
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)], **extra)
        return ModelResponse(parts=[TextPart(json.dumps(payload, ensure_ascii=False))], **extra)
