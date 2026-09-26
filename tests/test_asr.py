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


def test_numbers_compare_however_the_tts_read_them():
    """Transcripts from experiments/m5_reading_check.py: the TTS reads each number its own
    way and the recognizer writes what it heard."""
    same = [("他是1998年出生的。", "他是一九九八年出生的。"),
            ("剑身上刻着1234个小字。", "剑身上刻着一千两百三十四个小字。"),
            ("这座城有12,000户人家，共35000人。", "这座城有一万两千户人家，共三万五千人。"),
            ("他一年挣1.5万元。", "他一年挣一点五万元。"),
            ("成功率只有5%，失败率是12.5%。", "成功率只有百分之五，失败率是百分之十二点五。"),
            ("比赛最后以3:2结束。", "比赛最后以三比二结束。"),
            ("早上8:30出发，晚上20:15到达。", "早上八点半出发，晚上二十点十五到达。"),
            ("他的电话是13812345678。", "他的电话是一三八幺二三四五六七八。"),
            ("他每天跑5km，体重70kg。", "他每天跑五公里，体重七十公斤。"),
            ("今天最高30℃。", "今天最高三十摄氏度。"),
            ("只剩下1/3的粮食了。", "只剩下三分之一的粮食了。"),
            ("一张票¥100，换成美元大约$14。", "一张票一百元，换成美元大约十四美元。"),
            ("要走3-5天才能到。", "要走三到五天才能到。"),
            ("他住在302房间。", "他住在三零二房间。"),
            ("万一他不来呢？", "万一他不来呢？")]
    for text, heard in same:
        assert cer(text, heard, "zh") == 0.0, text
    assert cer("剑身上刻着1234个小字。", "剑身上刻着一千两百个小字。", "zh") > 0.05   # a real misreading


def test_regional_speech_is_not_retried_for_what_the_recognizer_cannot_write():
    """春尽江南 in the M3 emotion A/B: 国舅's 「……你发个话唦！」 and a Shanghainese line were
    retried three times each and kept with a warning."""
    guojiu = "你发个话，想怎么弄她就怎么弄她，吾要么不出动，一出动就是翻天覆地。你发个话唦！"
    heard = "你发个话，想怎么弄他就怎么弄他。吾要么不出动，一出动就是翻天覆地。你发个话来！"
    assert judge(guojiu, heard, "zh", 0.10)[1] is None
    shanghai = "策难！侬格小赤佬，哪能格能副样子！侬以为侬是啥宁，弗来三格！"
    assert judge(shanghai, "策男农革小赤老，哪能革？能副样子？农以为农是啥宁？夫来三革。", "zh", 0.10)[1] is None
    assert "dialect line" in judge(shanghai, "完全不相干的一句话而已", "zh", 0.10)[1]     # garbage still fails
    # Standard speech keeps both checks.
    assert "lost ending" in judge("这件事我想了很久，还是觉得应该告诉你。", "这件事我想了很久，还是觉得应该告诉。", "zh", 0.10)[1]
    assert judge("你来吗？", "你来？", "zh", 0.10)[1] is not None


def test_a_dialect_remark_inside_standard_prose_keeps_the_checks():
    from huashuo.asr import dialect_line

    assert dialect_line("“侬晓得伐？阿拉明朝就要走了。”")
    prose = "信是从长安寄来的，信封上写着请于三月十五日前回电。他把信读了三遍，又看了看落款。" * 2
    assert not dialect_line(prose + "“侬晓得伐？”一个商人说。")


def test_english_numbers_compare_however_they_were_read():
    for text, heard in [("Chapter 1", "Chapter One."), ("CHAPTER IV", "Chapter four."),
                        ("He was twenty-one in 1998.", "He was 21 in nineteen ninety eight."),
                        ("It cost 105 pounds.", "It cost one hundred and five pounds."),
                        ("In 2006 they left.", "In two thousand and six they left.")]:
        assert cer(text, heard, "en") == 0.0, text
    assert cer("I said so.", "I said so.", "en") == 0.0         # the pronoun is not a numeral
    assert judge("Chapter 1", "Chapter One.", "en", 0.10)[1] is None
    assert cer("‘You’re late,’ said Tom.", "You're late, said Tom.", "en") == 0.0


def test_characters_that_sound_the_same_compare_equal():
    """在桥上 (a real short novel): 他/她/它 alone caused 71 substitutions, a dozen retried
    units and three false lost endings, though speech cannot tell them apart."""
    same = [("她同意他的话。", "他同意他的话。"),
            ("他还会以残存的希望再次问她：", "他还会以残存的希望再次问他。"),     # was a "lost ending"
            ("他就是这样天真地笑着问她：", "他就是这样天真的笑着问他。"),
            ("只有一支笔，它在桌上。", "只有一只笔，他在桌上。"),
            ("汽车驶过大桥。", "汽车使过大桥。")]
    for text, heard in same:
        assert judge(text, heard, "zh", 0.10)[1] is None, text
    # Real misreadings still fail.
    assert judge("他停顿了一下，嗓音沙沙地继续说道：", "他停顿了一下，嗓沙地继续说道。", "zh", 0.10)[1]
    assert "lost ending" in judge("这件事我想了很久，还是觉得应该告诉你。", "这件事我想了很久，还是觉得应该告诉。",
                                  "zh", 0.10)[1]


def test_a_lone_numeral_is_compared_by_its_sound():
    """一条被洗澡水拍死的鱼: 「栖息的栖」 heard as 「七夕的七」, both qīxī."""
    assert judge("“你忘了？我叫栖芒，栖息的栖，芒果的芒。”", "你忘了，我叫七芒，七夕的七，芒果的芒。", "zh", 0.10)[1] is None
    assert cer("他考了第3名。", "他考了第三名。", "zh") == 0.0
    assert cer("一年只有一次。", "一年只有一次。", "zh") == 0.0
