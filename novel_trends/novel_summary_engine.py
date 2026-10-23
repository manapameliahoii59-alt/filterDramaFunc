# -*- coding: utf-8 -*-
"""
novel_trends/novel_summary_engine.py
基于大模型（DashScope Qwen3.8-Flash / DeepSeek）提取网文 6 维爆款基因（DNA）的核心引擎。
输入：前 5~6 章完整正文 + 前 40 章目录标题（连载不足有多少取多少） + 简介与元数据。
输出：严格符合 JSON Schema 的 6 大模块结构化信息。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv
import requests

# 优先定位项目根目录下的 .env，防止外部启动时找不到配置文件
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")


def _load_env_keys() -> Tuple[str, str, str]:
    """安全读取项目根目录 .env，获取 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_MODEL。"""
    env_file = _PROJECT_ROOT / ".env"
    env_map: Dict[str, str] = {}
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_map[k.strip()] = v.strip()
        except Exception:
            pass

    dashscope_key = os.environ.get("DASHSCOPE_API_KEY") or env_map.get("DASHSCOPE_API_KEY", "")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY") or env_map.get("DEEPSEEK_API_KEY", "")
    dashscope_model = os.environ.get("DASHSCOPE_MODEL") or env_map.get("DASHSCOPE_MODEL", "qwen3.8-flash")
    return dashscope_key, deepseek_key, dashscope_model

SYSTEM_PROMPT = """你是一个工业级的“网文与AI短剧/漫剧爆款基因（DNA）特征提取引擎”。
你的任务是：深入阅读用户提供的小说【前5~6章完整正文】及【前50章目录标题（连载不足有多少取多少）】，执行信噪比清洗，并严格按照指定的 JSON Schema 提炼出 6 大黄金维度结构化爆款 DNA。
提炼出的 6 大维度数据将直接用于与【第一集原素材（视频画面抽帧与台词字幕）】进行多级漏斗筛选、剧情骨架还原与核心脑洞比对。

### 6 大黄金维度提取清单与规范依据（必须严格遵守）：

1. 【数据质检与过滤 data_quality_check】(双轨制钩子与慢热排雷)
   - 提取来源依据：对比简介与前 6 章正文。
   - 在筛选“第一集原素材”时的实际用途：代码级自动过滤脏数据与极度慢热废书。
   - 字段规范：
     * is_synopsis_valid (布尔值): 简介是否有效。若简介是“简介无力请看正文”、文艺腔废话或与正文实际剧情严重割裂不符，填 false 并彻底无视简介；否则填 true。
     * hook_archetype (字符串): 开局核心钩子形态分类（双轨制识别），必须严格归入以下 3 类之一：
       ① "ACTION_SPECTACLE" (显性事件/外挂爽感型)：开局前 6 章发生明确的断亲退婚、系统觉醒、空间囤货、当众打脸、绝境反击或专业技能碾压立威。适合 AI 漫剧直拍。
       ② "EMOTIONAL_TENSION" (情绪暗涌/高张力拉扯型)：开局前 6 章具备高浓度的人际张力、疯批失控、熟龄情商博弈、身份错位伪装、强制爱或宿命拉扯（如晋江/女频高口碑言情）。适合高阶言情/前置剪辑。
       ③ "ATMOSPHERE_SLOW_BURN" (纯生活流/慢热沉浸型)：通篇无核心矛盾主线、平淡日常记账买菜、缺乏戏剧性对抗与情感暗流，属于纯慢热生活流。
     * hook_archetype_desc (字符串): 简要说明开局具备的具体钩子形态与戏剧张力所在（30字以内）。
     * has_hook_or_leverage_in_opening (布尔值): 前 6 章是否已亮出危机或戏剧张力钩子。若 hook_archetype 属于 ACTION_SPECTACLE 或 EMOTIONAL_TENSION，填 true；若属于 ATMOSPHERE_SLOW_BURN，填 false。
     * extraction_confidence (字符串): 提取把握度，仅限填 "高" / "中" / "低"。若正文严重缺失、篇幅过短或剧情混乱难以断定，填 "低"。

2. 【赛道与世界观 track_dna】
   - 提取来源依据：原站标签 + 前 6 章正文。
   - 在筛选“第一集原素材”时的实际用途：第一层漏斗粗筛。把原素材和小说按“末日囤货 / 真假千金 / 规则怪谈 / 都市神豪”分池，同赛道内进行比对，避免跨赛道乱匹配。
   - 字段规范：
     * gender_track (字符串): 男频 / 女频 / 泛性别。
     * worldview_setting (字符串): 世界观基底（如：现代都市 / 末日天灾 / 规则怪谈 / 玄幻修仙 / 古代架空 / 脑洞变异 / 赛博科幻 / 悬疑惊悚）。
     * core_sub_genre (字符串): 核心细分流派（如：末世囤货安全屋 / 真假千金断亲 / 全家偷听心声 / 神豪返利系统 / 战神隐忍退婚 / 四个首富哥哥团宠 / 闪婚千亿总裁）。

3. 【主角人设与破局底牌 protagonist_and_leverage_dna】(男女频双栖兼容)
   - 提取来源依据：前 6 章正文 + 前 15 章目录。
   - 在筛选“第一集原素材”时的实际用途：核心脑洞与设定匹配（向量检索）。兼容男频（系统面板/异能）与女频（偷听心声/空间/首富血缘/重生先知/闪婚大佬），直接比对原素材第一集亮出的核心设定。
   - 字段规范：
     * surface_status (字符串): 开局（第 1~2 章）主角受迫害弱势状态（如：替假少爷顶罪坐牢归来一身伤病的病弱弃子 / 被掉包回归豪门遭全家嫌弃污蔑的乡下真千金）。
     * hidden_or_awakened_identity (字符串): 真实隐藏身份或觉醒身份（如：唯一SSS级异能掌控者 / 跨国财阀唯一继承人 / 重生先知者 / 隐世神医传人）。
     * leverage_category (字符串): 破局底牌 6 大分类之一，必须严格归入以下 6 类之一：
       ① 系统面板类（返利/签到/百倍暴击/国运绑定等）
       ② 重生先知类（知晓未来剧本/重生记忆差/提前囤货等）
       ③ 异能空间类（全家偷听心声/随身无限空间/透视鉴宝/读心术等）
       ④ 隐藏马甲类（首富团宠/战神殿主/隐世神医/顶级黑客等）
       ⑤ 契约羁绊类（闪婚千亿总裁/冲喜契约/四个首富哥哥团宠/豪门联姻等）
       ⑥ 极致智商类（反向整顿职场/高智商疯批/专业刑侦技能反杀等）
     * leverage_specific_mechanism (字符串): 底牌具体运作逻辑。详细描述正文中展现的底牌具体运作机制与触发条件（如：“只要遭到假少爷挑衅贬低即可触发万倍返利并夺取对方气运”、“家里人能听到女主的心声从而提前识破绿茶假千金阴谋”）。

4. 【开局冲突与爽点走向 conflict_and_payoff_dna】(破局与闭环分离)
   - 提取来源依据：
     • 11~14 严格看 前 6 章正文
     • 15 重点看 第 16~50 章目录
   - 在筛选“第一集原素材”时的实际用途：对齐第一集剧本骨架 + 验证后续爽感。用 11~14 精准匹配第一集原素材的“受虐→破局立威”剧情；用 15 验证这本小说后续高潮够不够爽。
   - 字段规范：
     * antagonist_relationship (字符串): 迫害者关系。开局第一顺位迫害者与主角的人物关系（如：偏心生母与绿茶假千金 / 势利丈母娘与吸血小舅子 / 抢功背叛的前女友）。
     * inciting_incident (字符串): 第一集导火索危机。第 1 章正在发生的危机冲突（如：全家逼迫刚出狱的主角捐肾给假少爷 / 逼迫真千金代替养女嫁给残疾植物人 / 极寒末日倒计时主角被赶出家门）。
     * extreme_aggro_behavior (字符串): 反派拉仇恨言行。反派极度拉仇恨的具体恶行或道德绑架话术逻辑（如：一边霸占主角亡母抚恤金，一边把主角赶出家门冻死在雪地里 / 撕毁录取通知书逼主角下跪认错）。
     * opening_breakthrough_action (字符串): 开篇破局动作。前 5~6 章内主角完成的第一次实质性破局动作、立威或打脸反杀（如：当场撕毁断亲书反手买下整个宴会厅打脸全场 / 召唤SSS级黑龙当场捏碎退婚书 / 激活空间当夜清空全家百亿物资）。
     * subsequent_payoff_trend (字符串): 后续打脸闭环走向。重点结合第 16~50 章目录推演后续高潮与打脸闭环（如：极寒末日全家冻馁上门乞讨被主角在恒温堡垒内吃火锅无视打脸 / 假千金连环翻车全家痛哭火葬场跪求原谅 / 主角登顶首富将仇人家族彻底送进监狱）。

5. 【第一集音画匹配锚点 ep1_matching_anchors】
   - 提取来源依据：严格锁定第 1~2 章正文！
   - 在筛选“第一集原素材”时的实际用途：跨模态硬碰撞抓手！直接拿去撞第一集原素材的【画面抽帧物体】和【ASR台词文本】，命中率极高。
   - 字段规范：
     * key_props_and_scenes (字符串数组): 提取 4~8 个视频画面中肉眼可见的具象道具、场景词（如：断亲书、B超单、百亿黑卡、极寒暴风雪、系统全息弹窗、精神病院铁门、满屋囤积的米面粮油）。严禁填写抽象形容词！
     * core_dialogue_themes (字符串数组): 提取 3~5 个第一集高频出现的台词吵架主题（如：逼让保送名额、嘲讽主角吃软饭入赘、末日倒计时疯狂借贷囤货、当众宣读退婚书）。
     * character_archetypes (字符串数组): 提取出场人物典型人设（如：偏心生母、绿茶养妹、吸血鬼前女友、冷酷首富小叔、嚣张富二代）。

6. 【二级爆款标签树 hierarchical_tropes】
   - 提取来源依据：前 6 章正文 + 前 50 章目录（新书连载不足50章则有多少参考多少）。
   - 在筛选“第一集原素材”时的实际用途：统计热榜风向标。聚类统计近期日榜高频词（如 #开局签断亲书、#清空百亿物资），给命中的原素材额外加权。
   - 字段规范：
     * l1_macro_tropes (字符串数组): 3~5 个宏观母题标签，带 # 号（如：#真假千金, #末日囤货, #战神归来, #全员读心, #绝不原谅）。
     * l2_micro_elements (字符串数组): 8~12 个微观情节看点标签，带 # 号，提取具体情节动作、名场面（如：#开局签断亲书, #清空百亿物资, #雨夜罚跪, #当众退婚打脸, #打造末日避难所, #全家火葬场）。

### 必须严格遵循的纯净 JSON 输出 Schema：
{
  "title": "书名",
  "author": "作者",
  "data_quality_check": {
    "is_synopsis_valid": true,
    "hook_archetype": "ACTION_SPECTACLE / EMOTIONAL_TENSION / ATMOSPHERE_SLOW_BURN",
    "hook_archetype_desc": "简述开局核心钩子形态与张力所在",
    "has_hook_or_leverage_in_opening": true,
    "extraction_confidence": "高"
  },
  "track_dna": {
    "gender_track": "男频 / 女频 / 泛性别",
    "worldview_setting": "世界观基底（如：现代都市 / 末日天灾 / 规则怪谈 / 玄幻修仙 / 古代架空 / 脑洞变异）",
    "core_sub_genre": "核心细分流派（如：末世囤货安全屋 / 真假千金断亲 / 全家偷听心声 / 神豪返利系统 / 战神隐忍退婚 / 四个首富哥哥团宠）"
  },
  "protagonist_and_leverage_dna": {
    "surface_status": "开局受迫害弱势状态",
    "hidden_or_awakened_identity": "隐藏/觉醒身份",
    "leverage_category": "系统面板类 / 重生先知类 / 异能空间类 / 隐藏马甲类 / 契约羁绊类 / 极致智商类",
    "leverage_specific_mechanism": "底牌具体运作逻辑"
  },
  "conflict_and_payoff_dna": {
    "antagonist_relationship": "迫害者关系",
    "inciting_incident": "第一集导火索危机",
    "extreme_aggro_behavior": "反派拉仇恨言行",
    "opening_breakthrough_action": "开篇破局动作",
    "subsequent_payoff_trend": "后续打脸闭环走向"
  },
  "ep1_matching_anchors": {
    "key_props_and_scenes": [
      "具象道具或场景1", "具象道具或场景2"
    ],
    "core_dialogue_themes": [
      "台词吵架主题1", "台词吵架主题2"
    ],
    "character_archetypes": [
      "典型人设1", "典型人设2"
    ]
  },
  "hierarchical_tropes": {
    "l1_macro_tropes": [
      "#宏观母题1", "#宏观母题2"
    ],
    "l2_micro_elements": [
      "#微观情节1", "#微观情节2"
    ]
  }
}
输出要求：直接输出标准 JSON，禁止任何前缀后缀说明，严禁包含 ```json 等 Markdown 标记，不得缺失任何字段。"""


def build_user_prompt(
    title: str,
    author: str,
    tags: str,
    synopsis: str,
    chapter_titles: List[str],
    chapter_contents: List[Tuple[str, str]],
) -> str:
    """按指定规范组装 User Prompt 一次性发给模型。

    chapter_titles: 包含前 50 章标题列表（新书连载不满 50 章有多少取多少）
    chapter_contents: 包含 (章节标题, 章节正文) 的元组列表，一般取前 5~6 章
    """
    cleaned_titles = []
    for idx, t in enumerate(chapter_titles[:50], start=1):
        t_clean = (t or "").strip()
        if not t_clean:
            continue
        cleaned_titles.append(t_clean if t_clean.startswith("第") else f"第{idx}章 {t_clean}")
    titles_block = "\n".join(cleaned_titles)
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

【前50章章节目录（新书连载不足50章则提供现有全部目录；第1~15章用于提炼破局底牌，第16~50章用于提炼后续打脸闭环走向与爆款微观标签）】：
{titles_block}

【前6章完整正文（100%核心事实提取依据，用于提炼开篇危机、反派言行与破局动作，以及第1~2章音画匹配锚点）】：
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
    timeout_sec: int = 120,
) -> Tuple[bool, Dict[str, Any] | str]:
    """调用大模型（优先 DashScope Qwen3.8-Flash，故障/超时自动切备用 DeepSeek）提取 6 维 DNA。"""
    dashscope_key, deepseek_key, dashscope_model = _load_env_keys()

    errors = []

    # 1. 尝试 DashScope (Qwen3.8-Flash，需显式关闭 thinking 避免耗费过长时间输出思考 Token 导致超时)
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
                    "enable_thinking": False,
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
        return False, "未配置 DASHSCOPE_API_KEY 或 DEEPSEEK_API_KEY，请检查项目根目录 .env 文件。"

    return False, "；".join(errors) if errors else "大模型调用未成功返回有效 JSON 数据。"


def get_candidate_books_stats_from_db(platform: Optional[str] = None) -> Dict[str, Any]:
    """查询数据库中章节已就绪网文的 DNA 提取全量统计（未处理数、已入库数、平台细分分布）。"""
    from novel_trends.pg_storage import PgNovelStorage

    storage = PgNovelStorage()
    sql = """
    SELECT 
        sub.platform,
        count(*) as total_ready,
        count(*) FILTER (WHERE d_count = 0) as undone_count,
        count(*) FILTER (WHERE d_count > 0) as done_count
    FROM (
        SELECT c.platform, c.book_id, count(d.id) as d_count
        FROM novel_chapters c
        LEFT JOIN novel_intros i ON i.platform = c.platform AND i.book_id = c.book_id
        LEFT JOIN (
            SELECT DISTINCT ON (platform, book_id) platform, book_id, title, author
            FROM crawled_ranking_books
            ORDER BY platform, book_id, id DESC
        ) b ON b.platform = c.platform AND b.book_id = c.book_id
        LEFT JOIN novel_dna_records d ON d.book_key = (COALESCE(NULLIF(i.title, ''), b.title, '') || '_' || COALESCE(NULLIF(i.author, ''), b.author, ''))
        GROUP BY c.platform, c.book_id
        HAVING count(c.id) FILTER (WHERE length(COALESCE(c.content, '')) >= 50) >= 5
    ) sub
    GROUP BY sub.platform;
    """
    stats = {
        "total_undone": 0,
        "total_done": 0,
        "total_ready": 0,
        "by_platform": {},
        "current_platform": platform or "全部平台",
        "current_undone": 0,
        "current_total": 0,
    }
    try:
        with storage.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                for plat, ready, undone, done in cur.fetchall():
                    stats["total_undone"] += int(undone or 0)
                    stats["total_done"] += int(done or 0)
                    stats["total_ready"] += int(ready or 0)
                    stats["by_platform"][plat] = {
                        "total_ready": int(ready or 0),
                        "undone": int(undone or 0),
                        "done": int(done or 0),
                    }

        if platform and platform != "全部平台":
            plat_data = stats["by_platform"].get(platform, {})
            stats["current_undone"] = plat_data.get("undone", 0)
            stats["current_total"] = plat_data.get("total_ready", 0)
        else:
            stats["current_undone"] = stats["total_undone"]
            stats["current_total"] = stats["total_ready"]
    except Exception as exc:
        print(f"[novel_summary_engine] 查询候选书籍统计失败: {exc}")

    return stats


def fetch_candidate_books_from_db(
    limit: int = 150,
    platform: Optional[str] = None,
    only_undone: bool = False,
) -> List[Dict[str, Any]]:
    """从 PostgreSQL 中查询出已经抓取章节、适合进行 DNA 深度提取的书籍列表。

    支持按平台筛选，以及仅筛选未提取 DNA 的小说（要求有效正文章节数>=5）。
    """
    from novel_trends.pg_storage import PgNovelStorage

    storage = PgNovelStorage()
    where_clauses = []
    params: list = []

    if platform and platform != "全部平台":
        where_clauses.append("c.platform = %s")
        params.append(platform)

    having_clauses = ["count(c.id) FILTER (WHERE length(COALESCE(c.content, '')) >= 50) >= 5"]
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
           count(c.id) FILTER (WHERE length(COALESCE(c.content, '')) >= 50) as ch_cnt,
           d.id IS NOT NULL as has_dna,
           d.dna_profile
    FROM novel_chapters c
    LEFT JOIN novel_intros i ON i.platform = c.platform AND i.book_id = c.book_id
    LEFT JOIN (
        SELECT DISTINCT ON (platform, book_id) platform, book_id, title, author, category, intro
        FROM crawled_ranking_books
        ORDER BY platform, book_id, id DESC
    ) b ON b.platform = c.platform AND b.book_id = c.book_id
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

    1. 从 DB 加载前 40 章目录与前 6 章正文
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
            "message": f"正文章节不足（有效正文仅 {len(contents)} 章），无法分析开篇爽点，请先在【小说风向标】补抓该书章节正文",
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
    hook_type = str(dq.get("hook_archetype", "")).strip().upper()
    has_hook = dq.get("has_hook_or_leverage_in_opening")

    # 双轨制智能校准：显性事件爽感型 (ACTION_SPECTACLE) 与 情绪暗涌张力型 (EMOTIONAL_TENSION) 均判定为具备有效钩子
    if "EMOTIONAL_TENSION" in hook_type or "ACTION_SPECTACLE" in hook_type:
        has_hook = True
    elif "ATMOSPHERE_SLOW_BURN" in hook_type:
        has_hook = False
    elif has_hook is None:
        has_hook = dq.get("has_payoff_in_opening", True)

    confidence = str(dq.get("extraction_confidence", "高")).strip()
    is_low_conf = ("低" in confidence or confidence.lower() == "low")

    # 数据质检与过滤：若 has_hook 为 false（极度慢热文）或提取把握度为 低，直接丢弃不入库
    if discard_slow_burn:
        if not has_hook:
            hook_desc = dq.get("hook_archetype_desc") or "前6章属于纯生活流慢热，无有效危机或情感暗涌"
            return {
                "success": True,
                "status": "SLOW_BURN_DISCARDED",
                "message": f"极度慢热文（{hook_desc}），代码直接丢弃不入库",
                "elapsed": elapsed,
                "dna": dna_result,
                "saved_to_db": False,
            }
        if is_low_conf:
            return {
                "success": True,
                "status": "LOW_CONFIDENCE_DISCARDED",
                "message": f"提取把握度为低（{confidence}），代码直接丢弃不入库",
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
    """从 PostgreSQL 抓取该书籍的前 40 章标题与前 6 章完整正文，以及真实榜单排位数据。"""
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

                # 2. 查询前 50 章目录与前 6 章正文
                cur.execute(
                    """
                    SELECT chapter_index, title, content
                    FROM novel_chapters
                    WHERE platform = %s AND book_id = %s
                    ORDER BY chapter_index ASC
                    LIMIT 50;
                    """,
                    (platform, book_id),
                )
                rows = cur.fetchall()
                for r in rows:
                    ch_idx, ch_title, ch_content = r[0], r[1] or "", r[2] or ""
                    # 动态智能兜底：若标题为空或仅为纯数字占位，尝试从正文首行提取真实章节标题
                    if (not ch_title or ch_title.strip() in ("", f"第{ch_idx}章", f"第{ch_idx}章 第 {ch_idx} 章")) and ch_content:
                        first_line = ch_content.strip().split("\n")[0].strip()
                        m_t = re.match(r"^(\d+)[、.]\s*(.+)$", first_line)
                        if m_t:
                            c_num = m_t.group(1)
                            t_part = m_t.group(2).strip()
                            t_part = re.sub(r"[.…]{2,}$", "", t_part).strip()
                            t_part = re.sub(r"^第\s*\d+\s*章\s*", "", t_part).strip()
                            if len(t_part) > 40:
                                t_part = t_part[:40].strip()
                            ch_title = f"第{c_num}章 {t_part}".strip()
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
    worldview = track.get("worldview_setting") or track.get("main_category", "都市")
    sub_genre = track.get("core_sub_genre") or track.get("sub_category", "通用")

    protagonist = (
        dna_result.get("protagonist_and_leverage_dna")
        or dna_result.get("protagonist_and_cheat_dna")
        or dna_result.get("identity_contrast", {})
    )
    target_audience = protagonist.get("surface_status", "")

    worldview_setting = track.get("worldview_setting") or worldview
    core_sub_genre = track.get("core_sub_genre") or sub_genre
    leverage_category = protagonist.get("leverage_category") or protagonist.get("cheat_mechanism_type", "")
    conflict = dna_result.get("conflict_and_payoff_dna", {})
    opening_action = conflict.get("opening_breakthrough_action") or conflict.get("first_counterattack_payoff", "")
    subsequent_payoff = conflict.get("subsequent_payoff_trend", "")
    first_payoff = opening_action
    dq = dna_result.get("data_quality_check", {})
    hook_type = str(dq.get("hook_archetype", "")).strip().upper()
    has_hook = dq.get("has_hook_or_leverage_in_opening")
    if "EMOTIONAL_TENSION" in hook_type or "ACTION_SPECTACLE" in hook_type:
        has_hook = True
    elif "ATMOSPHERE_SLOW_BURN" in hook_type:
        has_hook = False
    elif has_hook is None:
        has_hook = dq.get("has_payoff_in_opening", True)
    has_hook = bool(has_hook)
    extraction_confidence = str(dq.get("extraction_confidence", "高")).strip()

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
        worldview_setting, core_sub_genre, leverage_category,
        first_counterattack_payoff, opening_breakthrough_action, subsequent_payoff_trend,
        has_hook_or_leverage_in_opening, extraction_confidence,
        l1_macro_tropes, l2_micro_elements,
        dna_profile, updated_at
    ) VALUES (
        %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s,
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
        worldview_setting = EXCLUDED.worldview_setting,
        core_sub_genre = EXCLUDED.core_sub_genre,
        leverage_category = EXCLUDED.leverage_category,
        first_counterattack_payoff = EXCLUDED.first_counterattack_payoff,
        opening_breakthrough_action = EXCLUDED.opening_breakthrough_action,
        subsequent_payoff_trend = EXCLUDED.subsequent_payoff_trend,
        has_hook_or_leverage_in_opening = EXCLUDED.has_hook_or_leverage_in_opening,
        extraction_confidence = EXCLUDED.extraction_confidence,
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
                        worldview_setting,
                        core_sub_genre,
                        leverage_category,
                        first_payoff,
                        opening_action,
                        subsequent_payoff,
                        has_hook,
                        extraction_confidence,
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
