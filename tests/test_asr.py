from huashuo.asr import cer, judge, lost_ending


def test_comparison_handles_traditional_characters_and_digits():
    assert cer("我從鄉下跑到京城里，後來打折了腿了。", "我从乡下跑到京城里，后来打折了腿了", "zh") == 0.0
    assert cer("他坐著。1987年", "他坐着，一九八七年", "zh") == 0.0
    assert cer("孔乙己是站著喝酒而穿長衫的唯一的人。", "孔乙己是站着喝酒的人", "zh") > 0.3


def test_english_ignores_case_and_punctuation():
    assert cer("It was the best of times.", "it was the best of times", "en") == 0.0


def test_a_lost_last_word_fails_the_check_under_the_error_threshold():
    line = "这件事我想了很久，还是觉得应该告诉你。"
    heard = "这件事，我想了很久，还是觉得应该告诉。"          # old_woman_stern, M3 stability run
    rate, problem = judge(line, heard, "zh", 0.10)
    assert rate < 0.10 and "lost ending" in problem
    assert judge(line, line, "zh", 0.10) == (0.0, None)
    assert not lost_ending("你先坐下，喝口热茶，慢慢说。", "你先坐下，喝口热茶，慢慢说吧。", "zh")  # an added particle
    assert not lost_ending("我從鄉下來。", "我从乡下来", "zh")
    assert lost_ending("Tell me what you saw.", "tell me what you", "en")
    assert not lost_ending("Tell me what you saw.", "Tell me what you saw, okay", "en")
    assert "ASR mismatch (29%)" in judge("雪下了整整一夜。", "雪下了一夜。", "zh", 0.10)[1]
    assert not lost_ending("地流走，什么意思么？", "的流走，什么意思吗？", "zh")   # 么 heard as 吗
