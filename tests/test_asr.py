from huashuo.asr import cer


def test_comparison_handles_traditional_characters_and_digits():
    assert cer("我從鄉下跑到京城里，後來打折了腿了。", "我从乡下跑到京城里，后来打折了腿了", "zh") == 0.0
    assert cer("他坐著。1987年", "他坐着，一九八七年", "zh") == 0.0
    assert cer("孔乙己是站著喝酒而穿長衫的唯一的人。", "孔乙己是站着喝酒的人", "zh") > 0.3


def test_english_ignores_case_and_punctuation():
    assert cer("It was the best of times.", "it was the best of times", "en") == 0.0
