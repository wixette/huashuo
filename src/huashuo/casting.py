"""Automatic casting: a voice for every character (CAST-4 … CAST-9, SCR-9).

Characters are ranked by how much they speak. The top `main` characters each get a voice
of their own, different from each other and from the narrator (CAST-6). Everyone else
shares voices, matched on gender and age, avoiding the voice of anyone they talk to
directly and the voices of main characters where possible (CAST-7). A first-person
narrator who also speaks (「我」, "I") keeps the narrator's voice, since the narration is
that character's own voice (SCR-9).

Voices the user chose by hand are fixed first and never changed; casting works around
them. Deterministic: the same cast always gets the same voices.
"""

from __future__ import annotations

from collections import Counter

from huashuo.library import AGES, Voice, castable

DEFAULT_MAIN = 8
FIRST_PERSON = {"我", "I", "me"}
_NARRATOR_WORDS = ("第一人称叙述者", "第一人称的叙述者", "叙述者「我」", "first-person narrator", "the narrator")


def is_first_person(name: str, character: dict) -> bool:
    """The character who narrates: named 「我」 / "I", or so described. Models often use
    the narrator's real name once another character says it (林默, aliases ['我', '默儿'],
    「第一人称叙述者……」), so aliases and the description count too (SCR-9)."""
    if name in FIRST_PERSON or FIRST_PERSON & set(character.get("aliases") or []):
        return True
    description = (character.get("description") or "").lower()
    return any(word.lower() in description for word in _NARRATOR_WORDS)
_AGE_INDEX = {age: i for i, age in enumerate(AGES)}


# The most-speaking character is the protagonist when ahead of the next by this factor
# (春尽江南: 谭端午 417 lines, 庞家玉 298, a ratio of 1.4).
PROTAGONIST_LEAD = 1.3


def choose_narrator(characters: dict[str, dict], language: str) -> tuple[str, str]:
    """The narrator voice for a book nobody chose one for, and why (CAST-4, SCR-9).

    A first-person book is narrated by one of its characters, so the narrator voice
    matches that character's gender. Otherwise it matches the protagonist's, when one
    character clearly speaks the most. Else, and for languages without narrator voices,
    the library's default narrator.
    """
    from huashuo.library import default_narrator, narrators

    default = default_narrator(language)
    by_gender = {}
    for voice in narrators(language):
        if voice.gender not in by_gender or voice.ref == default:
            by_gender[voice.gender] = voice.ref
    if not by_gender:
        return default, "default"
    for name, character in characters.items():
        if is_first_person(name, character):
            # The narration is this character's voice; other characters do not decide it.
            if character.get("gender") in by_gender:
                return by_gender[character["gender"]], f"first-person narrator {name} is {character['gender']}"
            return default, f"first-person narrator {name}, gender unknown: default"
    order = sorted(characters, key=lambda n: -int(characters[n].get("lines") or 0))
    if order:
        top = characters[order[0]]
        lines = int(top.get("lines") or 0)
        runner_up = int(characters[order[1]].get("lines") or 0) if len(order) > 1 else 0
        if lines and lines >= PROTAGONIST_LEAD * runner_up and top.get("gender") in by_gender:
            return by_gender[top["gender"]], (f"protagonist {order[0]} is {top['gender']} "
                                              f"({lines} lines, next {runner_up})")
    return default, "default"


def conversations(blocks: list[dict]) -> Counter:
    """How often each pair of speakers talks back to back (within a chapter)."""
    pairs: Counter = Counter()
    previous = None
    for block in blocks:
        kind = block.get("type")
        if kind == "chapter":
            previous = None
        elif kind == "dialogue":
            speaker = block.get("speaker")
            if speaker and speaker != "unknown":
                if previous and previous != speaker:
                    pairs[frozenset((previous, speaker))] += 1
                previous = speaker
    return pairs


def chapters_of(blocks: list[dict]) -> dict[str, set[str]]:
    """The chapters (by chapter block id) in which each speaker has a line."""
    seen: dict[str, set[str]] = {}
    chapter = None
    for block in blocks:
        if block.get("type") == "chapter":
            chapter = block.get("id")
        elif block.get("type") == "dialogue" and block.get("speaker") not in (None, "unknown"):
            seen.setdefault(block["speaker"], set()).add(chapter)
    return seen


# A character with this many lines or fewer may borrow a main character's voice when the
# two never speak in the same chapter: listeners cannot confuse people who never meet on
# the page, and the few free adult voices are spared for the bit parts that do.
BORROW_MAX_LINES = 2


def _mismatch(character: dict, voice: Voice) -> int:
    score = 0
    gender = character.get("gender")
    if gender in ("male", "female") and voice.gender != gender:
        score += 100
    age = character.get("age")
    target = _AGE_INDEX.get(age)
    if target is None:
        # Unknown age almost always means an adult (children are usually marked as such):
        # a child's or teenager's voice for a 主任 or a 老师 is far worse than sharing.
        score += _UNKNOWN_AGE_COST[voice.age]
    else:
        score += 20 * abs(_AGE_INDEX[voice.age] - target)
    return score


_UNKNOWN_AGE_COST = {"child": 80, "teen": 50, "young_adult": 0, "middle_aged": 0, "elderly": 35}
# Spreading bit parts over voices is nice, but never at the price of a wrong age: the
# penalty for a crowded voice stops growing below the cost of a wrong-age voice.
_USE_COST, _MAX_USE_COST = 8, 32


def cast_voices(characters: dict[str, dict], narrator: str, language: str,
                talks: Counter | None = None, fixed: dict[str, str] | None = None,
                main: int = DEFAULT_MAIN, pool: list[Voice] | None = None,
                suggested: dict[str, str] | None = None,
                chapters: dict[str, set[str]] | None = None) -> dict[str, str]:
    """Voice reference for every character.

    `suggested` are picks for main characters that also weigh personality (from the LLM,
    see attribution.suggest_voices). A suggestion is taken when it is castable, fits the
    character's gender, and is not already some other main character's voice; otherwise
    the rules below decide.
    """
    talks = talks or Counter()
    fixed = dict(fixed or {})
    pool = [v for v in (pool if pool is not None else castable(language)) if v.ref != narrator]
    order = sorted(characters, key=lambda n: (-int(characters[n].get("lines") or 0), n))
    main_names = [n for n in order if not is_first_person(n, characters[n])][:main]

    chosen: dict[str, str] = {}
    usage: Counter = Counter()
    main_voices: set[str] = set()
    for name, voice in fixed.items():
        if name in characters:
            chosen[name] = voice
            usage[voice] += 1
            if name in main_names:
                main_voices.add(voice)

    by_ref = {v.ref: v for v in pool}
    for name in main_names:
        pick = (suggested or {}).get(name)
        if name in chosen or pick not in by_ref or usage[pick]:
            continue
        gender = characters[name].get("gender")
        if gender in ("male", "female") and by_ref[pick].gender != gender:
            continue
        chosen[name] = pick
        usage[pick] += 1
        main_voices.add(pick)

    for name in order:
        if name in chosen:
            continue
        if is_first_person(name, characters[name]):
            chosen[name] = narrator
            continue
        character = characters[name]
        is_main = name in main_names
        partners = {other for pair in talks if name in pair for other in pair if other != name}

        def cost(voice: Voice) -> tuple:
            score = _mismatch(character, voice)
            score += 40 * sum(talks[frozenset((name, p))] > 0 for p in partners if chosen.get(p) == voice.ref)
            if is_main:
                score += 10_000 if usage[voice.ref] else 0        # a main character's voice is its own
            else:
                # Hearing the protagonist's voice from a bit part is worse than an age
                # mismatch of two steps (40), so sharing a main voice costs more.
                if voice.ref in main_voices:
                    owners = [n for n in main_names if chosen.get(n) == voice.ref]
                    apart = chapters is not None and int(character.get("lines") or 0) <= BORROW_MAX_LINES and all(
                        not (chapters.get(name, set()) & chapters.get(owner, set())) for owner in owners)
                    score += 0 if apart else 45
                score += min(_USE_COST * usage[voice.ref], _MAX_USE_COST)
            return score, usage[voice.ref], voice.ref      # ties go to the less used voice

        if not pool:
            chosen[name] = narrator
            continue
        best = min(pool, key=cost)
        chosen[name] = best.ref
        usage[best.ref] += 1
        if is_main:
            main_voices.add(best.ref)
    return chosen
