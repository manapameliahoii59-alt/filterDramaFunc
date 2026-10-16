"""
core/funnel_screener.py
两阶段漏斗初筛与精筛主引擎 (Two-Stage Funnel Screening Engine)

流程架构：
1. 本地持久化缓存快速检索 (Cache First)；
2. 常读多账号池安全检索剧目详情与真实简介 (Changdu Sync)；
3. 【阶段一：文本极速快筛】调用大模型与网文趋势库，淘汰 60%~70% 的低质剧目；
4. 【阶段二：深度视听精筛】仅对高分剧提取第 1 集 60 秒音视频：
   - 阿里 SenseVoiceSmall 本地离线精准转写开篇真实台词；
   - 稠密光流与仿射单应性检测，一票否决 PPT 漫；
   - 融合台词张力与画面质量综合终评 (S/A/B/C/VETO)；
5. 全量落库至 SQLite screening_cache.db。
"""
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from .models import DramaCandidate, CoarseFilterResult, CoarseFilterConfig
from .ai_evaluator import AIDramaEvaluator
from .cache_manager import ScreeningCacheManager
from .dialogue_transcriber import DialogueTranscriber
from .detectors.ppt_detector import PPTDetector


try:
    from novel_trends.trend_matcher import TrendMatcher
except ImportError:
    from ..novel_trends.trend_matcher import TrendMatcher

try:
    from changdu_sync.account_pool import ChangduAccountPool
except ImportError:
    from ..changdu_sync.account_pool import ChangduAccountPool




class FunnelScreeningEngine:
    """两阶段漏斗筛选引擎"""

    def __init__(
        self,
        account_pool: Optional[ChangduAccountPool] = None,
        cache_manager: Optional[ScreeningCacheManager] = None,
        ai_evaluator: Optional[AIDramaEvaluator] = None,
        stage2_threshold: float = 55.0,
        skip_cache: bool = False,
        enable_video: bool = True
    ):
        self.account_pool = account_pool
        self.cache_mgr = cache_manager or ScreeningCacheManager()
        self.ai_evaluator = ai_evaluator or AIDramaEvaluator()
        self.trend_matcher = TrendMatcher()
        self.transcriber = DialogueTranscriber()
        self.ppt_detector = PPTDetector(CoarseFilterConfig())

        self.stage2_threshold = stage2_threshold
        self.skip_cache = skip_cache
        self.enable_video = enable_video

    def screen_single_drama(self, candidate: DramaCandidate) -> Dict[str, Any]:
        """对单部短剧执行完整的漏斗初筛流程"""
        title = candidate.title.strip()
        print(f"\n{'='*70}")
        print(f"🎬 正在开始筛选剧目: 《{title}》")
        print(f"{'='*70}")

        # -------------------------------------------------------------
        # 步骤 0: 检查本地持久化缓存 (Cache-First)
        # -------------------------------------------------------------
        if not self.skip_cache:
            cached = self.cache_mgr.get(title)
            if cached:
                print(f"  ⚡ [本地缓存命中] 该剧在 14 天内已完成筛选，直接返回评级: 【{cached.get('tier')}级 | {cached.get('final_score')}分】")
                return {
                    "title": title,
                    "book_id": cached.get("book_id"),
                    "abstract": cached.get("abstract"),
                    "episode_amount": cached.get("episode_amount"),
                    "category_text": cached.get("category_text"),
                    "dialogue_text": cached.get("dialogue_text"),
                    "is_ppt": bool(cached.get("is_ppt")),
                    "stage1_score": cached.get("stage1_score"),
                    "final_score": cached.get("final_score"),
                    "tier": cached.get("tier"),
                    "genre_summary": cached.get("genre_summary"),
                    "ai_review": cached.get("ai_review"),
                    "diagnostic_message": cached.get("diagnostic_message"),
                    "from_cache": True
                }

        # -------------------------------------------------------------
        # 步骤 1: 联动常读平台账号池获取剧目详情
        # -------------------------------------------------------------
        book_id = None
        abstract = candidate.abstract or ""
        episode_amount = candidate.total_episode_count or 0
        category_text = "未知"
        original_thumb_url = ""

        if self.account_pool:
            print(f"  🔍 [常读检索] 正在安全检索常读平台分销库...")
            cd_info = self.account_pool.search_drama(title)
            if cd_info:
                book_id = cd_info.get("book_id")
                abstract = cd_info.get("abstract") or abstract
                episode_amount = cd_info.get("total_episode_count") or episode_amount
                category_text = cd_info.get("category_text") or category_text
                original_thumb_url = cd_info.get("original_thumb_url") or ""
                print(f"  ✓ 常读匹配成功: ID={book_id} | 共{episode_amount}集 | 分类: {category_text}")
                print(f"    官方简介: {abstract[:80]}...")
            else:
                print(f"  ⚠ 常读大盘未搜索到完全匹配剧目，采用本地输入作为备选输入。")

        # -------------------------------------------------------------
        # 步骤 2: 【阶段一：文本极速快筛】
        # -------------------------------------------------------------
        print(f"  🧠 [阶段一快筛] 启动大模型进行文本张力、开篇钩子与网文库趋势比对...")
        trend_res = self.trend_matcher.match_drama_trend(drama_title=title, drama_content=abstract)
        trend_bonus = float(trend_res.get("bonus_score", 0.0))
        trend_name = ",".join(trend_res.get("matched_patterns", [])) or "通用设定"

        ai_res = self.ai_evaluator.evaluate_drama(
            title=title,
            abstract=abstract,
            episodes=episode_amount,
            ep1_dialogue=""
        )

        tension_score = float(ai_res.get("story_tension_score", 20.0))
        density_score = float(ai_res.get("narrative_density_score", 15.0))
        genre_summary = ai_res.get("genre_category", category_text)
        stage1_base = tension_score + density_score + 30.0

        # 融合网文趋势加成 (最高 +15 分)
        fused_stage1 = round(min(100.0, stage1_base * 0.85 + trend_bonus), 1)

        print(f"  📊 阶段一文本评分: {fused_stage1}分 (张力: {tension_score}/40 | 密度: {density_score}/30 | 趋势加成: +{trend_bonus}分 [{trend_name}])")



        # 漏斗分支判定：若低于阈值，直接淘汰，绝不浪费高危视频接口
        if fused_stage1 < self.stage2_threshold:
            print(f"  ❌ [漏斗截流] 阶段一得分 ({fused_stage1}分) 未达到精筛阈值 ({self.stage2_threshold}分)，直接淘汰出局，跳过视频下载！")
            result_data = {
                "title": title,
                "book_id": book_id,
                "abstract": abstract,
                "episode_amount": episode_amount,
                "category_text": category_text,
                "dialogue_text": "（阶段一未达标，未请求视频）",
                "is_ppt": False,
                "ppt_ratio": 0.0,
                "stage1_score": fused_stage1,
                "final_score": fused_stage1,
                "tier": "C" if fused_stage1 < 50 else "B",
                "genre_summary": genre_summary,
                "ai_review": ai_res.get("detailed_critique", "阶段一文本得分较低，核心钩子与反转不足"),
                "diagnostic_message": f"阶段一文本快筛淘汰 (得分: {fused_stage1} < {self.stage2_threshold})",
                "from_cache": False
            }
            self.cache_mgr.save(result_data)
            return result_data

        print(f"  ✓ [晋级阶段二] 阶段一文本表现优异 ({fused_stage1}分 >= {self.stage2_threshold}分)，进入深度视听精筛！")

        # -------------------------------------------------------------
        # 步骤 3: 【阶段二：深度视听精筛】
        # -------------------------------------------------------------
        ep1_dialogue = "（无台词数据）"
        is_ppt = False
        ppt_rigid_ratio = 0.0
        final_score = fused_stage1
        diagnostic = "正常通过阶段二精筛"

        if self.enable_video and self.account_pool and book_id:
            print(f"  📥 [物料提取] 启动浏览器渲染通道抓取第 1 集原生解密音画...")
            browser_media = self.account_pool.fetch_first_episode_media_browser(book_id, duration_sec=6.0)
            if browser_media and browser_media.get("success"):
                audio_path = browser_media.get("audio_path")
                frames_np = browser_media.get("frames_np", [])
                print(f"  ✓ 成功捕获原生解密媒体: 音频={'有' if audio_path else '无'}, 画面采样帧数={len(frames_np)}")

                # 3.1 PPT漫与静态轮播严格检测
                if frames_np and len(frames_np) >= 2:
                    print(f"  👁️ [画面质检] 运行光流与仿射单应性算子，检测是否为 PPT 漫...")
                    ppt_res = self.ppt_detector.run_detection(video_frames=frames_np)
                    ppt_rigid_ratio = ppt_res.metric_scores.get("ppt_rigid_ratio", 0.0)

                    if ppt_res.triggered_veto or ppt_rigid_ratio >= 0.65:
                        print(f"  🚫 [一票否决] 识别到严重 PPT 漫特征 (刚体平移缩放率: {ppt_rigid_ratio:.2f})！直接否决并降为低分！")
                        is_ppt = True
                        final_score = 15.0
                        tier = "C"
                        diagnostic = f"检测到低质PPT漫 (刚体率 {ppt_rigid_ratio:.2f})，一票否决"
                        ai_review = f"【PPT漫一票否决】：该剧画面为静态插画全局推拉平移，缺乏人物肢体与分层动态，属于劣质PPT漫，直接淘汰。"

                        result_data = {
                            "title": title,
                            "book_id": book_id,
                            "abstract": abstract,
                            "episode_amount": episode_amount,
                            "category_text": category_text,
                            "dialogue_text": "（PPT漫否决，不提取台词）",
                            "is_ppt": True,
                            "ppt_ratio": ppt_rigid_ratio,
                            "stage1_score": fused_stage1,
                            "final_score": 15.0,
                            "tier": "C",
                            "genre_summary": genre_summary,
                            "ai_review": ai_review,
                            "diagnostic_message": diagnostic,
                            "from_cache": False
                        }
                        self.cache_mgr.save(result_data)
                        return result_data

                # 3.2 本地离线语音识别转写第 1 集台词
                if audio_path and Path(audio_path).exists():
                    print(f"  🎙️ [语音转写] 启动阿里 SenseVoiceSmall 本地离线转写第 1 集开篇台词...")
                    trans_res = self.transcriber.transcribe(audio_path)
                    ep1_dialogue = trans_res.get("dialogue_text") or "（音频静音或无台词）"
                    print(f"  ✓ 识别到开篇台词: {ep1_dialogue[:100]}...")
                else:
                    ep1_dialogue = "（未能捕获到有效音频轨）"

                # 3.3 结合真实台词由大模型二次深度综合打分
                print(f"  🌟 [多模态综合] 结合真实开篇台词与简介，大模型生成终极评审决策...")
                deep_ai = self.ai_evaluator.evaluate_drama(
                    title=title,
                    abstract=f"{abstract}\n第一集开篇台词：{ep1_dialogue}",
                    episodes=episode_amount,
                    ep1_dialogue=ep1_dialogue,
                    visual_info={"ppt_rigid_ratio": ppt_rigid_ratio, "is_rigid_ppt": is_ppt}
                )
                deep_tension = float(deep_ai.get("story_tension_score", tension_score))
                deep_density = float(deep_ai.get("narrative_density_score", density_score))
                final_score = round(min(100.0, (deep_tension + deep_density + 30.0) * 0.85 + trend_bonus), 1)
                ai_review = deep_ai.get("brief_review", ai_res.get("brief_review", "大模型评估完成"))
            else:
                err_msg = browser_media.get("error") if browser_media else "未知原因"
                print(f"  ⚠ 浏览器通道捕获音画未成功 ({err_msg})，优雅降级为基于简介终评。")
                ai_review = ai_res.get("brief_review", "简介评估完成")
        else:
            ai_review = ai_res.get("brief_review", "简介评估完成")


        # 评定等级
        if final_score >= 85:
            tier = "S"
        elif final_score >= 70:
            tier = "A"
        elif final_score >= 50:
            tier = "B"
        else:
            tier = "C"

        print(f"  🏆 终审结论: 【{tier} 级 | {final_score}分】 | 题材定位: {genre_summary}")

        result_data = {
            "title": title,
            "book_id": book_id,
            "abstract": abstract,
            "episode_amount": episode_amount,
            "category_text": category_text,
            "dialogue_text": ep1_dialogue,
            "is_ppt": is_ppt,
            "ppt_ratio": ppt_rigid_ratio,
            "stage1_score": fused_stage1,
            "final_score": final_score,
            "tier": tier,
            "genre_summary": genre_summary,
            "ai_review": ai_review,
            "diagnostic_message": diagnostic,
            "from_cache": False
        }
        self.cache_mgr.save(result_data)
        return result_data
