"""
test_coarse_filter.py
粗筛各算子单元测试与基准验证
"""
import numpy as np
import cv2
import pytest
from core.models import CoarseFilterConfig
from core.detectors import (
    DensityDetector,
    VisualDefectDetector,
    DoodleDetector,
    PPTDetector,
    AudioGlitchDetector
)
from core.pipeline import CoarseFilterPipeline


def test_density_detector():
    config = CoarseFilterConfig()
    detector = DensityDetector(config)

    # 1. 正常高信息密度对白
    good_text = "三年之期已到，战神归位！当年江城苏家陷害我入狱，今天封锁全城，所有欠我的全部十倍奉还！"
    res_good = detector.detect(dialogue_text=good_text)
    assert not res_good.triggered_veto
    assert res_good.metric_scores["narrative_redundancy_ratio"] < 0.60

    # 2. 严重注水拉扯废话
    bad_text = "你敢打我？我打你怎么了？你真的敢打我！我就是打你怎么了！你敢打我？我打你怎么了！你真敢打我？我打你怎么了！"
    res_bad = detector.detect(dialogue_text=bad_text)
    assert res_bad.triggered_veto
    assert res_bad.reason_code == "DETECTED_LOW_INFORMATION_DENSITY"

    # 3. 残留 AI 分镜提示词标签
    polluted_text = "[镜头特写] 三年之期已到，龙帅归位！[转场: 机场轰鸣] 随我奔赴苏家寿宴！"
    res_polluted = detector.detect(dialogue_text=polluted_text)
    assert res_polluted.triggered_veto
    assert res_polluted.reason_code == "DETECTED_PROMPT_POLLUTION"


def test_character_detector():
    from core.detectors import CharacterDetector
    config = CoarseFilterConfig()
    detector = CharacterDetector(config)

    # 1. 正常具备主角与对抗冲突
    good_text = "三年之期已到，战神归位！当年江城苏家陷害我入狱，今天封锁全城，所有欠我的全部十倍奉还！"
    res_good = detector.detect(dialogue_text=good_text)
    assert not res_good.triggered_veto

    # 2. 缺失核心主角实体（纯风景散文说明文）
    scenery_text = "今天天气真好，远处的青山隐没在薄雾中，河水静静流淌，微风吹拂着树叶沙沙作响，阳光照耀在大地之上。"
    res_scenery = detector.detect(dialogue_text=scenery_text)
    assert res_scenery.triggered_veto
    assert res_scenery.reason_code == "DETECTED_NO_CLEAR_PROTAGONIST"

    # 3. 缺乏戏剧对抗冲突（平淡买菜做饭流水账）
    daily_text = "今天我和同事去前面的菜市场买了两个苹果和一把青菜，然后顺路回家做了饭，吃完饭洗了碗。"
    res_daily = detector.detect(dialogue_text=daily_text)
    assert res_daily.triggered_veto
    assert res_daily.reason_code == "DETECTED_LACK_DRAMATIC_CONFLICT"



def test_visual_defect_detector():
    config = CoarseFilterConfig()
    detector = VisualDefectDetector(config)

    # 1. 正常清晰且有运动的图像帧（两帧不同，避免误判画面冻结）
    sharp_frame1 = np.random.randint(0, 255, (270, 480, 3), dtype=np.uint8)
    sharp_frame2 = np.random.randint(0, 255, (270, 480, 3), dtype=np.uint8)
    res_sharp = detector.detect(video_frames=[sharp_frame1, sharp_frame2])
    assert not res_sharp.triggered_veto

    # 2. 严重模糊图像帧 (高斯模糊滤波，两帧不同避免触发冻结)
    blur_frame1 = cv2.GaussianBlur(sharp_frame1, (51, 51), 0)
    blur_frame2 = cv2.GaussianBlur(sharp_frame2, (51, 51), 0)
    res_blur = detector.detect(video_frames=[blur_frame1, blur_frame2])
    assert res_blur.triggered_veto
    assert res_blur.reason_code == "DETECTED_SEVERE_BLUR"

    # 3. 全黑屏帧
    black_frame = np.zeros((270, 480, 3), dtype=np.uint8)
    res_black = detector.detect(video_frames=[black_frame, black_frame])
    assert res_black.triggered_veto
    assert res_black.reason_code == "DETECTED_SEVERE_BLACK_SCREEN"


def test_doodle_detector():
    config = CoarseFilterConfig()
    detector = DoodleDetector(config)

    # 1. 极简白底黑线简笔画/火柴人（纯白底+两条细线）
    doodle_frame = np.full((270, 480, 3), 255, dtype=np.uint8)
    cv2.line(doodle_frame, (100, 100), (200, 200), (0, 0, 0), 2)
    cv2.circle(doodle_frame, (150, 80), 20, (0, 0, 0), 2)
    
    res_doodle = detector.detect(video_frames=[doodle_frame, doodle_frame])
    assert res_doodle.triggered_veto
    assert res_doodle.reason_code == "DETECTED_ROUGH_DOODLE_OR_MEME"


def test_ppt_detector():
    config = CoarseFilterConfig()
    detector = PPTDetector(config)

    # 1. 模拟 PPT 漫（平移 Pan）：整张插画以固定速度均匀向右刚体平移
    base_frame = np.random.randint(50, 200, (240, 320, 3), dtype=np.uint8)
    cv2.circle(base_frame, (160, 120), 40, (255, 255, 255), -1)

    pan_frames = [
        cv2.warpAffine(base_frame, np.float32([[1, 0, i * 4], [0, 1, 0]]), (320, 240))
        for i in range(8)
    ]
    res_pan = detector.detect(video_frames=pan_frames)
    assert res_pan.triggered_veto
    assert res_pan.reason_code == "DETECTED_PPT_SLIDESHOW"

    # 2. 模拟 PPT 漫（推拉 Zoom）：整张插画以固定中心连续缩放
    zoom_frames = [
        cv2.warpAffine(base_frame, cv2.getRotationMatrix2D((160, 120), 0, 1.0 + i * 0.02), (320, 240))
        for i in range(8)
    ]
    res_zoom = detector.detect(video_frames=zoom_frames)
    assert res_zoom.triggered_veto
    assert res_zoom.reason_code == "DETECTED_PPT_SLIDESHOW"

    # 3. 模拟纯静态死图 PPT
    static_frames = [base_frame.copy() for _ in range(8)]
    res_static = detector.detect(video_frames=static_frames)
    assert res_static.triggered_veto
    assert res_static.reason_code == "DETECTED_PPT_SLIDESHOW"

    # 4. 模拟真实优质动态漫（局部五官/肢体动作，背景不动的多层运动）
    dynamic_frames = []
    for i in range(8):
        f = base_frame.copy()
        cv2.circle(f, (80 + i * 18, 100), 25, (255, 0, 0), -1)
        dynamic_frames.append(f)
    res_dynamic = detector.detect(video_frames=dynamic_frames)
    assert not res_dynamic.triggered_veto, "真动态漫局部运动不应被误判为PPT漫"


def test_audio_detector():
    config = CoarseFilterConfig()
    detector = AudioGlitchDetector(config)
    sr = 16000

    # 1. 静音数组 (3秒全0)
    silent_audio = np.zeros(sr * 3, dtype=np.float32)
    res_silent = detector.detect(audio_samples=silent_audio, sr=sr)
    assert res_silent.triggered_veto
    assert res_silent.reason_code == "DETECTED_AUDIO_SILENCE"

    # 2. 严重爆音削波失真 (连续截断在 1.0)
    clipped_audio = np.ones(sr * 2, dtype=np.float32)
    res_clip = detector.detect(audio_samples=clipped_audio, sr=sr)
    assert res_clip.triggered_veto
    assert res_clip.reason_code == "DETECTED_AUDIO_CLIPPING"


def test_pipeline_execution():
    pipeline = CoarseFilterPipeline()

    # 测试短路一票否决 (C 级淘汰)
    bloat_text = "你敢打我？我打你怎么了？你真的敢打我！我就是打你怎么了！你敢打我？我打你怎么了！"
    res = pipeline.filter_drama(dialogue_text=bloat_text)
    assert not res.passed
    assert res.action == "VETO"
    assert res.grade == "C"
    assert "DETECTED_LOW_INFORMATION_DENSITY" in res.veto_reasons
    assert res.total_execution_time_ms >= 0

    # 测试通过并进入精筛评级 (S/A/B 级)
    good_text = "三年之期已到，龙帅归位！江城苏家当年陷害我入狱，今天封锁全城，通知黑龙军团三千将士随我奔赴苏家寿宴！"
    res_good = pipeline.filter_drama(dialogue_text=good_text)
    assert res_good.passed
    assert res_good.action == "PASS"
    assert res_good.grade in ["S", "A"]
    assert res_good.fine_screening is not None
    assert res_good.fine_screening.total_score >= 65.0


def test_fine_ranker():
    from core.fine_ranker import FineRanker
    ranker = FineRanker()

    # 1. 爆款潜力 S 级剧目测试 (具备极端情绪反转、法庭反转、高密度推进)
    s_text = "十八年前掉包的恩怨，今天在法庭上彻底清算。法官大人，这是当年产房护士的亲笔供词和DNA原始采样记录！真正执掌顾氏财团百分之五十一股权的人，是我！"
    res_s = ranker.evaluate(s_text)
    assert res_s.grade == "S"
    assert res_s.total_score >= 80.0
    assert len(res_s.highlights) >= 2

    # 2. 保本优质 A 级剧目测试 (标准爽剧打脸)
    a_text = "三年之期已到，战神归位！当年江城苏家陷害我入狱，今天通知将士，清算一切！"
    res_a = ranker.evaluate(a_text)
    assert res_a.grade in ["S", "A"]
    assert res_a.total_score >= 65.0

    # 3. 平庸但无违规 B 级剧目测试
    b_text = "今天天气很好，张三去菜市场买了两个苹果和一把青菜，然后回家做了一顿饭。"
    res_b = ranker.evaluate(b_text)
    assert res_b.grade in ["B", "C"]


def test_spec_detector():
    from core.detectors import SpecDetector
    config = CoarseFilterConfig()
    detector = SpecDetector(config)

    # 1. 正常合格体量规格
    res_good = detector.detect(dialogue_text="《战神归来：龙帅出狱》[共80集|首集90秒]：三年之期已到，龙帅归位！")
    assert not res_good.triggered_veto

    # 2. 总集数不足 35 集 (30集)
    res_few_ep = detector.detect(dialogue_text="《短剧测试》[共30集|首集90秒]：三年之期已到，龙帅归位！")
    assert res_few_ep.triggered_veto
    assert res_few_ep.reason_code == "DETECTED_FEW_EPISODES"

    # 3. 第一集时长不足 50 秒 (30秒)
    res_short = detector.detect(dialogue_text="《快餐短剧》[共80集|首集30秒]：三年之期已到，龙帅归位！")
    assert res_short.triggered_veto
    assert res_short.reason_code == "DETECTED_SHORT_EPISODE_1"

    # 4. PPT漫不再凭文本标签一票否决（必须通过画面光流/仿射形变检测）
    res_ppt = detector.detect(dialogue_text="《极品家丁动态PPT漫》[共80集|首集90秒]：三年之期已到，龙帅归位！")
    assert not res_ppt.triggered_veto, "SpecDetector 不应根据文本标签误判 PPT 漫，应交由 PPTDetector 依据画面判断"

    # 5. 沙雕漫标签一票否决
    res_doodle = detector.detect(dialogue_text="《沙雕修仙传：火柴人表情包漫》[共80集|首集90秒]：三年之期已到，龙帅归位！")
    assert res_doodle.triggered_veto
    assert res_doodle.reason_code == "DETECTED_ROUGH_DOODLE_OR_MEME"


