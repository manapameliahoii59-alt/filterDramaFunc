# -*- coding: utf-8 -*-
"""
novel_trends/novel_summary_engine.py
基于大模型（DashScope Qwen3.8-Flash / DeepSeek）提取网文 6 维爆款基因（DNA）的核心引擎。
输入：前 5~6 章完整正文 + 前 20 章目录标题 + 简介与元数据。
输出：严格符合 JSON Schema 的 6 大模块结构化信息。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv
import requests

load_dotenv(".env")

SYSTEM_PROMPT = """你是一个工业级的“网文与AI短剧/漫剧爆款基因（DNA）特征提取引擎”。
你的任务是：深入阅读用户一次性提供的小说【前5~6章完整正文】及【前20章目录标题】，执行信噪比清洗，并严格按照指定的 JSON Schema 输出结构化爆款 DNA。该数据将用于与【未剪辑的AI短剧第一集原素材（台词与画面）】进行自动化特征碰撞筛选。

### 核心执行铁律（必须严格遵守）：
1. 【识别并无视假简介】：网文的 `原始简介` 极可能包含“简介无力请看正文”、文艺腔废话或虚假信息。你必须以【开篇完整正文】和【前20章目录标题】反映的实际剧情作为 100% 唯一事实依据！若简介无效或与正文不符，将 `is_synopsis_valid` 设为 `false` 并彻底忽略简介。
2. 【按章节分工提取（对齐短剧第一集浓缩节奏）】：短剧/漫剧的第一集通常会将小说前 5 章左右的剧情高密度浓缩。请按以下规则精准定位特征：
   - 紧盯【第1~2章正文】提取：主角开局弱势状态（`surface_status`）、第一集导火索事件（`inciting_incident`）、以及第一集画面道具与台词锚点（`ep1_matching_anchors`）。
   - 结合【第2~6章正文 + 前20章目录】提取：金手指/系统的具体运作规则（`cheat_specific_rule`）、隐藏身份（`hidden_or_awakened_identity`）、以及主角完成的第一次反击打脸爽点（`first_counterattack_payoff`）。
3. 【锚点必须具象可感知】：提取 `key_props_and_scenes` 时，必须提取在视频画面中“肉眼可见”的具体物品、特效或场景（如：断绝关系协议书、B超单、极寒暴风雪、系统全息面板、百亿黑卡），严禁填写抽象形容词。
4. 【纯净 JSON 输出】：仅输出合法的、可被程序直接解析的 JSON 字符串。严禁包含 ```json 等 Markdown 代码块标记，严禁输出任何解释性文字，不得缺失任何字段。

### 必须严格遵循的 JSON Schema 结构：
{
  "title": "字符串，书名",
  "author": "字符串，作者",
  "data_quality_check": {
    "is_synopsis_valid": true,
    "has_payoff_in_opening": true,
    "extraction_confidence": "高 / 中 / 低（若正文严重缺失或乱码则填低）"
  },
  "track_dna": {
    "gender_track": "男频 / 女频 / 泛性别",
    "worldview_setting": "世界观基底（如：现代都市 / 末日天灾 / 规则怪谈 / 玄幻修仙 / 古代架空 / 脑洞变异）",
    "core_sub_genre": "核心细分流派（如：末世囤货安全屋 / 真假千金断亲 / 全家偷听心声 / 神豪返利系统 / 战神隐忍退婚）"
  },
  "protagonist_and_cheat_dna": {
    "surface_status": "开局（第1-2章）主角的弱势/受迫害状态（如：替假少爷顶罪坐牢归来一身伤病的弃子）",
    "hidden_or_awakened_identity": "开篇揭露的真实隐藏身份或觉醒身份（如：唯一SSS级空间觉醒者 / 顶级财阀亲生继承人 / 重生先知者）",
    "cheat_mechanism_type": "破局外挂分类（系统面板类 / 重生先知记忆差 / 特殊异能如读心透视 / 随身空间物资 / 无外挂纯靠大佬身份或智商）",
    "cheat_specific_rule": "详细描述正文中展现的外挂具体运作机制与触发条件（如：只要遭到亲人贬低就能获得现金暴击并剥夺对方气运 / 家里人能听到婴儿女主的心声从而看穿反派阴谋）"
  },
  "conflict_and_payoff_dna": {
    "antagonist_relationship": "开局第一顺位迫害者与主角的人物关系（如：偏心眼亲生父母与绿茶假千金 / 嫌贫爱富的未婚妻一家）",
    "inciting_incident": "开局核心导火索危机（第1章正在发生的具体冲突，如：全家逼迫刚出狱的主角捐出一颗肾给假少爷）",
    "extreme_aggro_behavior": "反派极度拉仇恨的具体恶行或道德绑架话术逻辑（如：一边霸占主角的抚恤金，一边把主角赶出家门冻死在雪地里）",
    "first_counterattack_payoff": "前5~6章内主角完成的第一次实质性反击、立威或打脸爽点事件（如：当场签下断亲书断绝供给，反手在拍卖会上用百亿黑卡碾压反派）"
  },
  "ep1_matching_anchors": {
    "key_props_and_scenes": [
      "从前2章为主的正文中提取4~8个极具视觉辨识度的具体道具、特效或场景锚点（如：断绝关系协议书、B超单、百亿黑卡、极寒暴风雪、系统觉醒面板、精神病院大门、满屋囤积的米面粮油）"
    ],
    "core_dialogue_themes": [
      "从正文中提取3~5个第一集高频出现的台词冲突主题（如：逼迫让出保送名额、嘲讽主角吃软饭、末日倒计时疯狂借贷囤货、当众宣读退婚书）"
    ],
    "character_archetypes": [
      "提取开篇核心出场人物的典型人设标签（如：偏心亲妈、绿茶养妹、吸血鬼前女友、冷酷首富小叔、嚣张富二代）"
    ]
  },
  "hierarchical_tropes": {
    "l1_macro_tropes": [
      "3~5个宏观爆款母题标签，带#号，如：#末日囤货, #重生复仇, #全家火葬场, #系统神豪, #全员读心"
    ],
    "l2_micro_elements": [
      "8~12个开篇微观情节看点标签，带#号，结合前6章正文与前20章目录提取具体动作与名场面，如：#开局签断亲书, #变卖百亿家产囤货, #当众退婚打脸, #系统百倍返还, #打造末日安全屋"
    ]
  }
}"""


def build_user_prompt(
    title: str,
    author: str,
    tags: str,
    synopsis: str,
    chapter_titles: List[str],
    chapter_contents: List[Tuple[str, str]],
) -> str:
    """按指定规范组装 User Prompt 一次性发给模型。

    chapter_titles: 包含前 20 章标题列表
    chapter_contents: 包含 (章节标题, 章节正文) 的元组列表，一般取前 5~6 章
    """
    titles_block = "\n".join(
        f"第{idx+1}章 {t}" if not t.startswith("第") else t
        for idx, t in enumerate(chapter_titles[:20])
    )
    if not titles_block.strip():
        titles_block = "（暂缺章节目录）"

    chapters_block_parts = []
    for idx, (ch_title, ch_text) in enumerate(chapter_contents[:6], start=1):
        clean_text = ch_text.strip() if ch_text else "（本章暂缺正文）"
        chapters_block_parts.append(f"=== 第{idx}章：{ch_title} ===\n{clean_text}")

    chapters_block = "\n\n".join(chapters_block_parts) if chapters_block_parts else "（暂缺正文）"

    return f"""请深度阅读以下抓取到的小说多源数据，并严格按照 System Prompt 中的 JSON Schema 提取其爆款 DNA：

【书名】：《{title.strip()}》
【作者】：{author.strip() or '未知'}
【原站分类与标签】：{tags.strip() or '通用'}

【原始简介（注意：可能存在乱写或无效废话，请务必与正文核对）】：
{synopsis.strip() or '（无原始简介）'}

【前20章章节标题目录（用于把握中前期爽点走向）】：
{titles_block}

【前6章完整正文（100%核心事实提取依据）】：
{chapters_block}"""


def _clean_json_text(text: str) -> str:
    """清洗大模型返回的 markdown 代码块与前后杂质。"""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    # 查找首个 '{' 和末个 '}'
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        cleaned = cleaned[first_brace : last_brace + 1]
    return cleaned


def call_llm_extract_dna(
    user_prompt: str,
    system_prompt: str = SYSTEM_PROMPT,
    timeout_sec: int = 90,
) -> Tuple[bool, Dict[str, Any] | str]:
    """调用大模型（优先 DashScope Qwen3.8-Flash，故障/超时自动切备用 DeepSeek）提取 6 维 DNA。"""
    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    dashscope_model = os.getenv("DASHSCOPE_MODEL", "qwen3.8-flash").strip()

    errors = []

    # 1. 尝试 DashScope
    if dashscope_key:
        try:
            resp = requests.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {dashscope_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": dashscope_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2500,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout_sec,
            )
            if resp.status_code == 200:
                raw_content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(_clean_json_text(raw_content))
                return True, parsed
            else:
                errors.append(f"DashScope HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as e:
            errors.append(f"DashScope 请求异常: {e}")

    # 2. 尝试 DeepSeek
    if deepseek_key:
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {deepseek_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2500,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout_sec,
            )
            if resp.status_code == 200:
                raw_content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(_clean_json_text(raw_content))
                return True, parsed
            else:
                errors.append(f"DeepSeek HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as e:
            errors.append(f"DeepSeek 请求异常: {e}")

    if not dashscope_key and not deepseek_key:
        return False, "未配置 DASHSCOPE_API_KEY 或 DEEPSEEK_API_KEY，请检查 .env 文件。"

    return False, "；".join(errors) if errors else "大模型调用未成功返回有效 JSON 数据。"


def fetch_candidate_books_from_db(
    limit: int = 150,
    platform: Optional[str] = None,
    only_undone: bool = False,
) -> List[Dict[str, Any]]:
    """从 PostgreSQL 中查询出已经抓取章节、适合进行 DNA 深度提取的书籍列表。

    支持按平台筛选，以及仅筛选未提取 DNA 的小说。
    """
    from novel_trends.pg_storage import PgNovelStorage

    storage = PgNovelStorage()
    where_clauses = []
    params: list = []

    if platform and platform != "全部平台":
        where_clauses.append("c.platform = %s")
        params.append(platform)

    having_clauses = ["count(c.id) >= 6"]
    if only_undone:
        having_clauses.append("bool_and(d.id IS NULL)")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    having_sql = f"HAVING {' AND '.join(having_clauses)}"

    sql = f"""
    SELECT c.platform, c.book_id,
           COALESCE(NULLIF(i.title, ''), NULLIF(b.title, ''), '未知作品') as title,
           COALESCE(NULLIF(i.author, ''), NULLIF(b.author, ''), '未知作者') as author,
           COALESCE(b.category, '全网热门') as category,
           COALESCE(i.intro, b.intro, '') as intro,
           count(c.id) as ch_cnt,
           d.id IS NOT NULL as has_dna,
           d.dna_profile
    FROM novel_chapters c
    LEFT JOIN novel_intros i ON i.platform = c.platform AND i.book_id = c.book_id
    LEFT JOIN crawled_ranking_books b ON b.platform = c.platform AND b.book_id = c.book_id
    LEFT JOIN novel_dna_records d ON d.book_key = (COALESCE(NULLIF(i.title, ''), b.title, '') || '_' || COALESCE(NULLIF(i.author, ''), b.author, ''))
    {where_sql}
    GROUP BY c.platform, c.book_id, i.title, b.title, i.author, b.author, b.category, i.intro, b.intro, d.id, d.dna_profile
    {having_sql}
    ORDER BY has_dna DESC, ch_cnt DESC, c.platform, c.book_id
    LIMIT %s;
    """
    params.append(limit if limit > 0 else 1000)

    results = []
    try:
        with storage.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                for row in cur.fetchall():
                    raw_dna = row[8]
                    dna_dict = None
                    if raw_dna:
                        dna_dict = raw_dna if isinstance(raw_dna, dict) else json.loads(raw_dna)
                    results.append({
                        "platform": row[0],
                        "book_id": row[1],
                        "title": row[2],
                        "author": row[3],
                        "category": row[4],
                        "intro": row[5],
                        "chapter_count": row[6],
                        "has_dna": bool(row[7]),
                        "dna_profile": dna_dict,
                    })
    except Exception as exc:
        print(f"[novel_summary_engine] 查询数据库书籍列表失败: {exc}")
    return results


def extract_dna_for_book(
    platform: str,
    book_id: str,
    discard_slow_burn: bool = True,
    timeout_sec: int = 90,
) -> Dict[str, Any]:
    """为单本小说执行端到端 DNA 提取与入库流程。

    1. 从 DB 加载前 20 章目录与前 6 章正文
    2. 组装 user prompt
    3. 调用大模型（Qwen3.8-Flash / DeepSeek）
    4. 质检与慢热过滤：若 has_payoff_in_opening 为 False 且 discard_slow_burn 为 True，则丢弃不入库
    5. 若合格，自动保存至 novel_dna_records
    """
    import time

    start_t = time.time()
    ctx = load_book_context_from_db(platform, book_id)
    title = ctx.get("title", "").strip()
    author = ctx.get("author", "").strip()
    category = ctx.get("category", "").strip()
    intro = ctx.get("intro", "").strip()
    titles = ctx.get("chapter_titles", [])
    contents = ctx.get("chapter_contents", [])

    if not title:
        return {
            "success": False,
            "status": "ERROR",
            "message": "未能获取该书籍的有效标题",
            "elapsed": round(time.time() - start_t, 2),
            "dna": None,
        }

    if len(contents) < 1:
        return {
            "success": False,
            "status": "MISSING_CHAPTERS",
            "message": f"正文章节不足（仅 {len(contents)} 章），无法执行开篇分析",
            "elapsed": round(time.time() - start_t, 2),
            "dna": None,
        }

    user_prompt = build_user_prompt(
        title=title,
        author=author,
        tags=category,
        synopsis=intro,
        chapter_titles=titles,
        chapter_contents=contents,
    )

    ok, payload = call_llm_extract_dna(user_prompt, timeout_sec=timeout_sec)
    elapsed = round(time.time() - start_t, 2)

    if not ok:
        return {
            "success": False,
            "status": "LLM_ERROR",
            "message": str(payload),
            "elapsed": elapsed,
            "dna": None,
        }

    dna_result = payload
    dq = dna_result.get("data_quality_check", {})
    has_payoff = dq.get("has_payoff_in_opening", True)

    if not has_payoff and discard_slow_burn:
        return {
            "success": True,
            "status": "SLOW_BURN_DISCARDED",
            "message": "开篇无爽点/极度慢热，代码直接丢弃不入库",
            "elapsed": elapsed,
            "dna": dna_result,
            "saved_to_db": False,
        }

    # 合格入库
    saved = save_dna_to_postgres(
        dna_result,
        platform=platform,
        rank_name=ctx.get("rank_name"),
        rank_position=ctx.get("rank_position", 1),
        board_type=ctx.get("board_type"),
        heat_score=ctx.get("heat_score"),
    )
    return {
        "success": True,
        "status": "QUALIFIED",
        "message": "开篇有爽点，特征合格，已保存入库至 PG",
        "elapsed": elapsed,
        "dna": dna_result,
        "saved_to_db": saved,
    }


def load_book_context_from_db(platform: str, book_id: str) -> Dict[str, Any]:
    """从 PostgreSQL 抓取该书籍的前 20 章标题与前 6 章完整正文，以及真实榜单排位数据。"""
    from novel_trends.pg_storage import PgNovelStorage

    storage = PgNovelStorage()
    context = {
        "platform": platform,
        "book_id": book_id,
        "title": "",
        "author": "",
        "category": "",
        "intro": "",
        "rank_name": "",
        "rank_position": 1,
        "board_type": "NEW_TREND",
        "heat_score": 85.0,
        "chapter_titles": [],
        "chapter_contents": [],
    }

    try:
        with storage.get_connection() as conn:
            with conn.cursor() as cur:
                # 1. 查询元数据（优先关联爬虫榜单真实排位）
                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(i.title, ''), NULLIF(b.title, ''), ''),
                           COALESCE(NULLIF(i.author, ''), NULLIF(b.author, ''), ''),
                           COALESCE(b.category, ''),
                           COALESCE(i.intro, b.intro, ''),
                           b.rank_name,
                           b.rank_position,
                           b.heat_score
                    FROM (SELECT %s AS platform, %s AS book_id) req
                    LEFT JOIN novel_intros i ON i.platform = req.platform AND i.book_id = req.book_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (platform, book_id)
                               platform, book_id, title, author, category, intro, rank_name, rank_position, heat_score
                        FROM crawled_ranking_books
                        ORDER BY platform, book_id, rank_position ASC
                    ) b ON b.platform = req.platform AND b.book_id = req.book_id
                    LIMIT 1;
                    """,
                    (platform, book_id),
                )
                meta = cur.fetchone()
                if meta:
                    context["title"] = meta[0] or ""
                    context["author"] = meta[1] or ""
                    context["category"] = meta[2] or ""
                    context["intro"] = meta[3] or ""
                    if meta[4]:
                        context["rank_name"] = meta[4]
                        context["rank_position"] = meta[5] or 1
                        if meta[6] is not None:
                            context["heat_score"] = float(meta[6])

                # 若通过 book_id 未能匹配到榜单信息，尝试通过书名和作者回查 crawled_ranking_books
                if not context.get("rank_name") and context.get("title"):
                    title = context["title"]
                    clean_title = title.replace("《", "").replace("》", "").strip()
                    author = context.get("author", "").strip()
                    cur.execute(
                        """
                        SELECT rank_name, rank_position, heat_score
                        FROM crawled_ranking_books
                        WHERE platform = %s
                          AND (title = %s OR title = %s OR (author = %s AND author != '' AND author != '未知'))
                        ORDER BY rank_position ASC
                        LIMIT 1;
                        """,
                        (platform, title, clean_title, author),
                    )
                    rank_row = cur.fetchone()
                    if rank_row:
                        context["rank_name"] = rank_row[0]
                        context["rank_position"] = rank_row[1] or 1
                        if rank_row[2] is not None:
                            context["heat_score"] = float(rank_row[2])

                rn = context.get("rank_name", "")
                if rn:
                    context["board_type"] = "NEW_TREND" if any(k in rn for k in ["新", "潜力"]) else "MONTHLY_HOT"
                else:
                    context["rank_name"] = "月度排行榜"
                    context["board_type"] = "MONTHLY_HOT"

                # 2. 查询前 20 章目录与前 6 章正文
                cur.execute(
                    """
                    SELECT chapter_index, title, content
                    FROM novel_chapters
                    WHERE platform = %s AND book_id = %s
                    ORDER BY chapter_index ASC
                    LIMIT 20;
                    """,
                    (platform, book_id),
                )
                rows = cur.fetchall()
                for r in rows:
                    ch_idx, ch_title, ch_content = r[0], r[1] or "", r[2] or ""
                    context["chapter_titles"].append(ch_title)
                    if ch_idx <= 6 and ch_content:
                        context["chapter_contents"].append((ch_title, ch_content))

    except Exception as exc:
        print(f"[novel_summary_engine] 获取书籍全量章节失败: {exc}")

    return context


def save_dna_to_postgres(
    dna_result: Dict[str, Any],
    platform: str = "大盘精选",
    rank_name: Optional[str] = None,
    rank_position: Optional[int] = None,
    board_type: Optional[str] = None,
    heat_score: Optional[float] = None,
) -> bool:
    """将大模型提取的 6 维 DNA 结果结构化存储到 PostgreSQL novel_dna_records 表。"""
    from novel_trends.pg_storage import PgNovelStorage

    storage = PgNovelStorage()
    title = dna_result.get("title", "").strip()
    author = dna_result.get("author", "").strip()
    if not title:
        return False

    clean_title = title.replace("《", "").replace("》", "").strip()

    # 如果未显式提供榜单名称或为旧占位符，则自动从 crawled_ranking_books 检索真实榜单
    if not rank_name or rank_name == "大模型深度提炼":
        try:
            with storage.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT rank_name, rank_position, heat_score
                        FROM crawled_ranking_books
                        WHERE (platform = %s OR %s = '大盘精选')
                          AND (title = %s OR title = %s OR (author = %s AND author != '' AND author != '未知'))
                        ORDER BY rank_position ASC
                        LIMIT 1;
                        """,
                        (platform, platform, title, clean_title, author),
                    )
                    row = cur.fetchone()
                    if row:
                        rank_name = row[0]
                        rank_position = row[1] or 1
                        if heat_score is None and row[2] is not None:
                            heat_score = float(row[2])
        except Exception as exc:
            print(f"[novel_summary_engine] 查询书籍榜单信息失败: {exc}")

    rank_name = rank_name or "月度排行榜"
    rank_position = rank_position or 1
    board_type = board_type or ("NEW_TREND" if any(k in rank_name for k in ["新", "潜力"]) else "MONTHLY_HOT")
    heat_score = heat_score if heat_score is not None else 85.0

    track = dna_result.get("track_dna", {})
    gender_track = track.get("gender_track", "男频")
    worldview = track.get("worldview_setting", "都市")
    sub_genre = track.get("core_sub_genre", "通用")

    protagonist = dna_result.get("protagonist_and_cheat_dna", {})
    target_audience = protagonist.get("surface_status", "")

    tropes = dna_result.get("hierarchical_tropes", {})
    l1 = tropes.get("l1_macro_tropes", [])
    l2 = tropes.get("l2_micro_elements", [])

    book_key = f"{title}_{author}"

    sql = """
    INSERT INTO novel_dna_records (
        book_key, title, author,
        source_platform, rank_name, rank_position, board_type,
        heat_score, days_on_rank, lifecycle_stage,
        gender_track, main_category, sub_category, target_audience,
        l1_macro_tropes, l2_micro_elements,
        dna_profile, updated_at
    ) VALUES (
        %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s,
        %s, NOW()
    )
    ON CONFLICT (book_key) DO UPDATE SET
        source_platform = EXCLUDED.source_platform,
        rank_name = EXCLUDED.rank_name,
        rank_position = EXCLUDED.rank_position,
        board_type = EXCLUDED.board_type,
        heat_score = EXCLUDED.heat_score,
        gender_track = EXCLUDED.gender_track,
        main_category = EXCLUDED.main_category,
        sub_category = EXCLUDED.sub_category,
        target_audience = EXCLUDED.target_audience,
        l1_macro_tropes = EXCLUDED.l1_macro_tropes,
        l2_micro_elements = EXCLUDED.l2_micro_elements,
        dna_profile = EXCLUDED.dna_profile,
        updated_at = NOW();
    """
    try:
        with storage.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        book_key,
                        title,
                        author,
                        platform,
                        rank_name,
                        rank_position,
                        board_type,
                        heat_score,
                        1,
                        "SUPER_EXPLODING",
                        gender_track,
                        worldview,
                        sub_genre,
                        target_audience[:120],
                        l1,
                        l2,
                        json.dumps(dna_result, ensure_ascii=False),
                    ),
                )
            conn.commit()
        return True
    except Exception as exc:
        print(f"[novel_summary_engine] 保存 DNA 至 PostgreSQL 失败: {exc}")
        return False
