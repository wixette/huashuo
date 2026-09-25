import json

import numpy as np
import pytest

from huashuo import library
from huashuo.library import LibraryError, all_voices, castable, get


def test_presets_and_library_voices_load(tiny_library):
    voices = all_voices()
    assert set(voices) == {"preset:serena", "preset:vivian", "preset:uncle_fu", "preset:dylan",
                           "library:zh/young_man", "library:zh/old_man"}
    old = get("library:zh/old_man")
    assert (old.kind, old.gender, old.age, old.language) == ("library", "male", "elderly", "zh")
    assert old.vector().shape == (2048,) and old.vector().dtype == np.float32
    assert get("preset:serena").kind == "preset" and get("preset:serena").vector_path is None


def test_castable_leaves_out_dialect_presets(tiny_library):
    refs = {v.ref for v in castable("zh")}
    assert "preset:dylan" not in refs and "library:zh/young_man" in refs and "preset:serena" in refs
    assert castable("en") == []


def test_unknown_voice_names_what_exists(tiny_library):
    with pytest.raises(LibraryError, match="known library voices: library:zh/old_man, library:zh/young_man"):
        get("library:zh/nobody")


def test_fingerprint_follows_the_vector(tiny_library):
    voice = get("library:zh/young_man")
    before = voice.fingerprint()
    np.save(tiny_library / "zh" / "young_man.npy", np.ones(2048, dtype=np.float32))
    assert voice.fingerprint() != before
    assert get("preset:vivian").fingerprint() == "preset:vivian"


def test_missing_vector_is_an_error(tiny_library):
    (tiny_library / "zh" / "lonely.json").write_text(json.dumps({"gender": "male", "age": "teen"}), encoding="utf-8")
    library._load.cache_clear()
    with pytest.raises(LibraryError, match="missing lonely.npy"):
        all_voices()


def test_narrator_voices_are_the_default_narrator_and_never_cast(tiny_library):
    import json

    import numpy as np

    import huashuo.library as library
    from huashuo.cast import default_cast

    assert default_cast("zh")["narrator"]["voice"] == "preset:serena"      # no narrator in the library: a preset
    for vid, default in (("reader", True), ("reader2", False)):
        (tiny_library / "zh" / f"{vid}.json").write_text(json.dumps(
            {"gender": "female", "age": "middle_aged", "role": vid, "traits": ["narrator"],
             **({"default_narrator": True} if default else {})}), encoding="utf-8")
        np.save(tiny_library / "zh" / f"{vid}.npy", np.ones(2048, dtype=np.float32))
    library._load.cache_clear()
    assert default_cast("zh")["narrator"]["voice"] == "library:zh/reader"
    assert default_cast("en")["narrator"]["voice"] == "preset:ryan"
    refs = {v.ref for v in library.castable("zh")}
    assert "library:zh/reader" not in refs and "library:zh/reader2" not in refs and "preset:serena" in refs
    assert [v.ref for v in library.narrators("zh")] == ["library:zh/reader", "library:zh/reader2"]


def test_the_packaged_library_has_its_narrators():
    from huashuo.library import castable, default_narrator, narrators

    assert default_narrator("zh") == "library:zh/narrator_female"
    assert {v.ref for v in narrators("zh")} == {"library:zh/narrator_female", "library:zh/narrator_male"}
    assert not {v.ref for v in narrators("zh")} & {v.ref for v in castable("zh")}
