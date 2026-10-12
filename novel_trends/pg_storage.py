# -*- coding: utf-8 -*-
"""
novel_trends/pg_storage.py
PostgreSQL 工业级持久化存储适配器
负责将网络小说 6 维 DNA 特征、两级分层母题热词、真实平台榜单与时效生命周期
存入 PostgreSQL (mypgsql) 中，并提供 GIN 倒排索引秒级匹配检索支持。
"""
import os
import json
import math
from typing import Dict, Any, List, Optional, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor

from .models import NovelDnaProfile


def _load_env_config() -> Dict[str, str]:
    """安全读取本地 .env 数据库配置"""
    config = {
        "PG_HOST": os.environ.get("PG_HOST", "129.204.86.63"),
        "PG_PORT": os.environ.get("PG_PORT", "5432"),
        "PG_DATABASE": os.environ.get("PG_DATABASE", "mypgsql"),
        "PG_USER": os.environ.get("PG_USER", "myPgSQL"),
        "PG_PASSWORD": os.environ.get("PG_PASSWORD", "aaa111222"),
    }
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k in config:
                            config[k] = v
        except Exception:
            pass
    return config


class PgNovelStorage:
    """PostgreSQL 网络小说 DNA 特征存储引擎"""

    def __init__(self):
        self.config = _load_env_config()

    def get_connection(self):
        """获取 psycopg2 数据库连接"""
        return psycopg2.connect(
            host=self.config["PG_HOST"],
            port=int(self.config["PG_PORT"]),
            dbname=self.config["PG_DATABASE"],
            user=self.config["PG_USER"],
            password=self.config["PG_PASSWORD"],
            connect_timeout=10
        )

    def init_database(self) -> bool:
        """
        初始化数据表与所有高性能索引 (DDL)
        包含:
        - 真实平台榜单排位
        - 方案 B 时效生命周期 (0~10天 / 11~30天 / 31~60天 / >60天)
        - 标量 B-Tree 索引
        - 两级母题 GIN 倒排索引
        - JSONB 深度分析字段
        """
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS novel_dna_records (
            id BIGSERIAL PRIMARY KEY,
            book_key VARCHAR(128) NOT NULL UNIQUE,          -- 唯一标识: {title}_{author}
            title VARCHAR(128) NOT NULL,                    -- 书名
            author VARCHAR(64) NOT NULL,                    -- 作者
            
            -- 1. 真实榜单排位数据源 (方式 1 核心输入)
            source_platform VARCHAR(32) NOT NULL DEFAULT '番茄小说', -- 番茄小说 / 起点中文网 / 七猫中文网 / 晋江文学城 / 潇湘书院 / 纵横中文网
            rank_name VARCHAR(32) NOT NULL DEFAULT '热读榜',         -- 热读榜 / 月票榜 / 新晋作者榜 / 阅读榜 / 畅销榜
            rank_position INT NOT NULL DEFAULT 1,                  -- 排名名次 (1 ~ 100)
            board_type VARCHAR(16) NOT NULL DEFAULT 'NEW_TREND',   -- NEW_TREND (新书新晋榜) / MONTHLY_HOT (月度主力榜) / RETENTION_HOT (高留存追读榜)
            
            -- 2. 大盘热度赋分 (由名次与生命周期衰减计算)
            heat_score NUMERIC(5, 2) NOT NULL DEFAULT 85.00,       -- (60.00 ~ 100.00)
            
            -- 3. 时效生命周期字段 (方案 B 极限制)
            first_ranked_at DATE DEFAULT CURRENT_DATE,              -- 首次上榜日期
            last_ranked_at DATE DEFAULT CURRENT_DATE,               -- 最近一次在榜日期
            days_on_rank INT DEFAULT 1,                             -- 累计在榜天数
            is_currently_on_rank BOOLEAN DEFAULT TRUE,              -- 当前是否在榜
            lifecycle_stage VARCHAR(16) DEFAULT 'EXPLODING',        -- EXPLODING (0~10天) / MATURE (11~30天) / SATURATED (31~60天) / DEPRECATED (>60天)

            -- 4. 赛道与受众标量 (B-Tree 索引)
            gender_track VARCHAR(16) NOT NULL,                      -- 男频 / 女频
            main_category VARCHAR(32) NOT NULL,                     -- 主题材 (都市 / 现言 / 古言 / 玄幻 / 悬疑)
            sub_category VARCHAR(64) NOT NULL,                      -- 细分赛道 (真假千金马甲 / 战神出狱 / 精神病院怪谈)
            target_audience VARCHAR(128) DEFAULT '',                -- 受众画像
            
            -- 5. 两级分层母题数组 (GIN 倒排索引极速比对)
            l1_macro_tropes TEXT[] NOT NULL DEFAULT '{}',           -- L1 宏观母题 (3~5个)
            l2_micro_elements TEXT[] NOT NULL DEFAULT '{}',         -- L2 微观剧情元素 (8~12个)
            
            -- 6. 完整 6 维结构化分析 JSONB
            dna_profile JSONB NOT NULL,
            
            -- 7. 风险与业务状态
            risk_level VARCHAR(32) DEFAULT '安全',
            status VARCHAR(16) DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- 建立高性能索引
        CREATE INDEX IF NOT EXISTS idx_novel_dna_track 
            ON novel_dna_records (gender_track, main_category);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_sub 
            ON novel_dna_records (sub_category);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_rank 
            ON novel_dna_records (source_platform, rank_position);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_lifecycle 
            ON novel_dna_records (lifecycle_stage, is_currently_on_rank);

        -- GIN 倒排索引 (支持母题标签微秒级数组包含与重合查询)
        CREATE INDEX IF NOT EXISTS idx_novel_dna_l1_gin 
            ON novel_dna_records USING GIN (l1_macro_tropes);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_l2_gin 
            ON novel_dna_records USING GIN (l2_micro_elements);

        -- JSONB 倒排索引
        CREATE INDEX IF NOT EXISTS idx_novel_dna_profile_gin 
            ON novel_dna_records USING GIN (dna_profile);
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(create_table_sql)
                conn.commit()
            print("[PgNovelStorage] PostgreSQL 表结构与 GIN 倒排索引初始化成功！")
            return True
        except Exception as e:
            print(f"[PgNovelStorage] 初始化表结构失败: {e}")
            return False

    @staticmethod
    def calculate_heat_score(rank_position: int, days_on_rank: int = 1) -> Tuple[float, str]:
        """
        根据榜单名次与在榜天数，按【方案 B】计算最终大盘热度与生命周期阶段
        - 0 ~ 10 天: 爆发期 (EXPLODING)，超频加权 1.10x
        - 11 ~ 30 天: 成熟期 (MATURE)，基准 1.00x
        - 31 ~ 60 天: 饱和疲劳期 (SATURATED)，衰减 0.75x
        - > 60 天: 过气淘汰期 (DEPRECATED)，衰减 0.40x
        """
        pos = max(1, rank_position)
        # 基础名次分: 100 - 15 * log10(pos)
        base_score = max(60.0, 100.0 - 15.0 * math.log10(pos))

        if days_on_rank <= 10:
            stage = "EXPLODING"
            decay_factor = 1.10
        elif days_on_rank <= 30:
            stage = "MATURE"
            decay_factor = 1.00
        elif days_on_rank <= 60:
            stage = "SATURATED"
            decay_factor = 0.75
        else:
            stage = "DEPRECATED"
            decay_factor = 0.40

        final_score = min(100.0, round(base_score * decay_factor, 2))
        return final_score, stage

    def upsert_novel_dna(
        self,
        profile: Dict[str, Any],
        source_platform: str = "番茄小说",
        rank_name: str = "热读榜",
        rank_position: int = 1,
        board_type: str = "NEW_TREND",
        days_on_rank: int = 3
    ) -> bool:
        """写入或更新单部小说的 DNA 记录 (幂等 Upsert)"""
        title = profile.get("title", "").strip()
        author = profile.get("author", "").strip()
        book_key = f"{title}_{author}"

        heat_score, lifecycle_stage = self.calculate_heat_score(rank_position, days_on_rank)

        track_info = profile.get("track_and_audience", {})
        gender_track = track_info.get("gender_track", "男频")
        main_category = track_info.get("main_category", "都市")
        sub_category = track_info.get("sub_category", "通用")
        target_audience = track_info.get("target_audience", "")

        tropes = profile.get("hierarchical_tropes", {})
        l1_tropes = tropes.get("l1_macro_tropes", [])
        l2_tropes = tropes.get("l2_micro_elements", [])

        compliance = profile.get("compliance_risk", {})
        risk_level = compliance.get("risk_level", "安全")

        sql = """
        INSERT INTO novel_dna_records (
            book_key, title, author,
            source_platform, rank_name, rank_position, board_type,
            heat_score, days_on_rank, lifecycle_stage,
            gender_track, main_category, sub_category, target_audience,
            l1_macro_tropes, l2_micro_elements,
            dna_profile, risk_level, updated_at
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, NOW()
        )
        ON CONFLICT (book_key) DO UPDATE SET
            source_platform = EXCLUDED.source_platform,
            rank_name = EXCLUDED.rank_name,
            rank_position = EXCLUDED.rank_position,
            board_type = EXCLUDED.board_type,
            heat_score = EXCLUDED.heat_score,
            days_on_rank = EXCLUDED.days_on_rank,
            lifecycle_stage = EXCLUDED.lifecycle_stage,
            gender_track = EXCLUDED.gender_track,
            main_category = EXCLUDED.main_category,
            sub_category = EXCLUDED.sub_category,
            target_audience = EXCLUDED.target_audience,
            l1_macro_tropes = EXCLUDED.l1_macro_tropes,
            l2_micro_elements = EXCLUDED.l2_micro_elements,
            dna_profile = EXCLUDED.dna_profile,
            risk_level = EXCLUDED.risk_level,
            updated_at = NOW();
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        book_key, title, author,
                        source_platform, rank_name, rank_position, board_type,
                        heat_score, days_on_rank, lifecycle_stage,
                        gender_track, main_category, sub_category, target_audience,
                        l1_tropes, l2_tropes,
                        json.dumps(profile, ensure_ascii=False), risk_level
                    ))
                conn.commit()
            return True
        except Exception as e:
            print(f"[PgNovelStorage] 写入《{title}》失败: {e}")
            return False

    def get_all_profiles(self) -> List[NovelDnaProfile]:
        """从 PostgreSQL 加载所有 active 状态小说的 DNA 模型"""
        sql = "SELECT dna_profile FROM novel_dna_records WHERE status = 'active' ORDER BY heat_score DESC;"
        profiles = []
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    for r in rows:
                        data = r["dna_profile"]
                        try:
                            p = NovelDnaProfile.parse_obj(data) if hasattr(NovelDnaProfile, "parse_obj") else NovelDnaProfile.model_validate(data)
                            profiles.append(p)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[PgNovelStorage] 查询小说列表异常: {e}")
        return profiles

    def search_by_tropes(self, candidate_tropes: List[str], channel: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        利用 GIN 倒排索引极速检索（微秒级命中母题）
        使用数组重叠操作符 && 查找命中的爆款小说
        """
        if not candidate_tropes:
            return []

        where_clauses = ["status = 'active'"]
        params: List[Any] = [candidate_tropes, candidate_tropes]

        if channel:
            where_clauses.append("gender_track = %s")
            params.append(channel)

        where_sql = " AND ".join(where_clauses)
        sql = f"""
        SELECT 
            title, author, source_platform, rank_name, rank_position,
            heat_score, lifecycle_stage, sub_category,
            l1_macro_tropes, l2_micro_elements
        FROM novel_dna_records
        WHERE (l1_macro_tropes && %s OR l2_micro_elements && %s)
          AND {where_sql}
        ORDER BY heat_score DESC;
        """
        results = []
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, params)
                    results = cur.fetchall()
        except Exception as e:
            print(f"[PgNovelStorage] 倒排索引检索异常: {e}")
        return results

    def get_count(self) -> int:
        """获取当前入库小说总数"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM novel_dna_records;")
                    return cur.fetchone()[0]
        except Exception:
            return 0

    def save_crawled_books(self, books: List[Any]) -> int:
        """
        批量将四大平台抓取到的实时榜单作品保存到 PostgreSQL 数据表 crawled_ranking_books
        """
        if not books:
            return 0

        ddl = """
        CREATE TABLE IF NOT EXISTS crawled_ranking_books (
            id BIGSERIAL PRIMARY KEY,
            book_id VARCHAR(64) NOT NULL,
            title VARCHAR(128) NOT NULL,
            author VARCHAR(64) NOT NULL,
            platform VARCHAR(32) NOT NULL,
            channel VARCHAR(16) NOT NULL,
            rank_name VARCHAR(32) NOT NULL,
            rank_position INT NOT NULL,
            category VARCHAR(64) DEFAULT '',
            intro TEXT DEFAULT '',
            book_url TEXT DEFAULT '',
            heat_score NUMERIC(5, 2) DEFAULT 80.00,
            fetched_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_platform_rank_pos UNIQUE (platform, rank_name, rank_position)
        );
        CREATE INDEX IF NOT EXISTS idx_crawled_platform ON crawled_ranking_books(platform, rank_name);
        CREATE INDEX IF NOT EXISTS idx_crawled_title ON crawled_ranking_books(title, author);
        """
        sql = """
        INSERT INTO crawled_ranking_books (
            book_id, title, author, platform, channel,
            rank_name, rank_position, category, intro, book_url,
            heat_score, fetched_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, NOW()
        )
        ON CONFLICT (platform, rank_name, rank_position) DO UPDATE SET
            book_id = EXCLUDED.book_id,
            title = EXCLUDED.title,
            author = EXCLUDED.author,
            category = EXCLUDED.category,
            intro = EXCLUDED.intro,
            book_url = EXCLUDED.book_url,
            heat_score = EXCLUDED.heat_score,
            fetched_at = NOW();
        """
        saved = 0
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(ddl)
                    for b in books:
                        cur.execute(sql, (
                            str(getattr(b, "book_id", "") or ""),
                            getattr(b, "title", "").strip(),
                            (getattr(b, "author", "未知") or "未知").strip(),
                            getattr(b, "platform", ""),
                            getattr(b, "channel", "男频"),
                            getattr(b, "rank_name", "热读榜"),
                            getattr(b, "rank_position", 1),
                            getattr(b, "category", "") or "",
                            getattr(b, "intro", "") or "",
                            getattr(b, "book_url", "") or "",
                            getattr(b, "heat_score", 80.0)
                        ))
                        saved += 1
                conn.commit()
            return saved
        except Exception as e:
            print(f"[PgNovelStorage] 批量写入抓取书籍失败: {e}")
            return 0

    def get_crawled_books_count(self) -> int:
        """获取已抓取存储的书籍总数"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM crawled_ranking_books;")
                    return cur.fetchone()[0]
        except Exception:
            return 0


# ============================================================================
# 5 大标杆网文基准数据 (涵盖男频与女频三大赛道)
# ============================================================================
BENCHMARK_NOVELS = [
    {
        "meta": {
            "source_platform": "番茄小说",
            "rank_name": "热读榜",
            "rank_position": 1,
            "board_type": "NEW_TREND",
            "days_on_rank": 3
        },
        "profile": {
            "title": "龙王出狱",
            "author": "不醉",
            "track_and_audience": {
                "gender_track": "男频",
                "main_category": "都市/战神",
                "sub_category": "战神回归/龙王出狱复仇",
                "target_audience": "下沉市场中青年男性，偏好打脸虐渣、战神归来、极致反差爽感用户"
            },
            "tone_and_intensity": {
                "narrative_pace": "极致快节奏",
                "emotional_tone": "极致爽快与强情绪反杀，打脸虐渣，当场报仇"
            },
            "identity_contrast": {
                "surface_identity": "刚出狱的劳改犯/被家族当弃子陷害入狱的底层青年",
                "hidden_identity": "执掌黑龙殿/统御三千将士/龙帅至尊/通天医圣",
                "contrast_gap": "从被所有人肆意羞辱的刑满释放犯，到权倾天下、封锁全城机场的至高龙帅，落差极致"
            },
            "opening_crisis_hook": {
                "conflict_type": "替家族顶罪入狱/女友背叛/寿宴被羞辱/母亲灵堂被砸",
                "crisis_description": "三年牢狱之灾替豪门女友顶罪，今日出狱却见女友嫁给仇敌，母亲重病被扫地出门，仇人逼主角当众下跪钻裤裆"
            },
            "payoff_mode": {
                "leverage": "黑龙战神令+通天权势+三千龙卫+通天医武神功",
                "core_payoff": "百倍奉还、寿宴碾压、当场下跪、打脸虐渣、绝不原谅"
            },
            "hierarchical_tropes": {
                "l1_macro_tropes": ["#龙王归来", "#战神出狱", "#打脸虐渣", "#战神赘婿", "#当场反杀"],
                "l2_micro_elements": [
                    "#三年之期已到", "#替人顶罪入狱", "#封锁全城机场", "#寿宴送钟",
                    "#黑龙令出", "#女友悔恨下跪", "#撕毁婚书", "#通天医术救大佬",
                    "#反派家族覆灭", "#当众打脸虐渣", "#百倍奉还", "#绝不姑息"
                ]
            },
            "compliance_risk": {
                "risk_level": "安全",
                "risk_reason": "合规安全"
            }
        }
    },
    {
        "meta": {
            "source_platform": "潇湘书院",
            "rank_name": "近7天阅读榜",
            "rank_position": 1,
            "board_type": "RETENTION_HOT",
            "days_on_rank": 4
        },
        "profile": {
            "title": "真千金她是全能大佬",
            "author": "卿浅",
            "track_and_audience": {
                "gender_track": "女频",
                "main_category": "都市/现言",
                "sub_category": "真假千金马甲/全能大佬逆袭",
                "target_audience": "偏好豪门真假千金、马甲掉马与打脸爽感的女性中青年，尤其下沉短剧女性用户"
            },
            "tone_and_intensity": {
                "narrative_pace": "极致快节奏",
                "emotional_tone": "极致爽快与强情绪反杀，打脸密集、绝不原谅"
            },
            "identity_contrast": {
                "surface_identity": "被抱错流落在外的乡下真千金，回归豪门后被当成土包子、冒牌货和家族耻辱",
                "hidden_identity": "全能大佬，医武双绝、顶级黑客、神秘财阀幕后、设计/医术/古武等多重马甲加身",
                "contrast_gap": "从被全家嫌弃、被假千金踩在脚底的乡巴佬，到各方势力跪求的全能大佬，身份反差拉满，每集掉马即打脸"
            },
            "opening_crisis_hook": {
                "conflict_type": "真假千金陷害/被逼签断绝关系书/替嫁/赶出家门",
                "crisis_description": "前60秒：真千金被认回豪门，假千金装柔弱当众泼脏水，父母偏信假千金，逼她下跪道歉、让出房间，甚至甩出断绝关系书逼她替嫁残疾老男人，雨夜将她赶出家门"
            },
            "payoff_mode": {
                "leverage": "隐藏马甲、专业技能、信息差、顶级人脉与神秘势力",
                "core_payoff": "打脸虐渣、全家悔恨、权谋反杀、绝不原谅"
            },
            "hierarchical_tropes": {
                "l1_macro_tropes": ["#真假千金", "#全能马甲", "#大女主逆袭", "#豪门打脸", "#断绝关系"],
                "l2_micro_elements": [
                    "#真千金被换", "#乡下回归被嫌", "#假千金装白莲", "#全家偏心逼跪",
                    "#逼签断绝关系书", "#替嫁残疾大佬", "#雨夜赶出家门", "#神医马甲掉马",
                    "#黑客马甲震惊全场", "#豪门父母下跪求原谅", "#假千金身份曝光", "#夺回母亲遗物绝不原谅"
                ]
            },
            "compliance_risk": {
                "risk_level": "安全",
                "risk_reason": "合规安全"
            }
        }
    },
    {
        "meta": {
            "source_platform": "起点中文网",
            "rank_name": "月票榜",
            "rank_position": 1,
            "board_type": "MONTHLY_HOT",
            "days_on_rank": 7
        },
        "profile": {
            "title": "青山",
            "author": "会说话的肘子",
            "track_and_audience": {
                "gender_track": "男频",
                "main_category": "都市/悬疑",
                "sub_category": "精神病院怪谈/异界偷渡/规则反制",
                "target_audience": "18-35岁年轻男性、网文核心读者、短剧悬疑微恐与反转爽剧受众"
            },
            "tone_and_intensity": {
                "narrative_pace": "快节奏，开篇即高能冲突与强悬念反转",
                "emotional_tone": "反套路发疯整顿 + 极度压抑反转 + 悬疑怪谈双线交织"
            },
            "identity_contrast": {
                "surface_identity": "父母双亡被亲戚构陷、诊断为重度PTSD关入精神病院六楼的被夺产少年",
                "hidden_identity": "高智商疯批破局者、被精神病异人选中的异界关键偷渡者",
                "contrast_gap": "表面是任亲戚宰割的疯人院孤儿，真实是把整个精神病院反向驯化、玩弄反派于股掌的幕后控场者"
            },
            "opening_crisis_hook": {
                "conflict_type": "亲戚夺产/被送精神病院/假诊断剥夺民事能力",
                "crisis_description": "父母车祸去世留数千万遗产，二叔二婶买通医生做假精神病诊断，强行将主角锁进青山精神疾病医院六楼放弃治疗区企图合法霸占豪宅与巨资。"
            },
            "payoff_mode": {
                "leverage": "极致冷静的高智商反套路 + 精神病院神秘病友异界偷渡线索",
                "core_payoff": "反向整顿疯人院、打脸贪婪亲戚、异界偷渡降维打击、绝不原谅"
            },
            "hierarchical_tropes": {
                "l1_macro_tropes": ["#精神病院怪谈", "#反向整顿", "#异界偷渡", "#豪门夺产", "#高智商疯批"],
                "l2_micro_elements": [
                    "#父母车祸留巨额遗产", "#二叔二婶伪造精神病报告", "#锁入六楼铁门",
                    "#四千万存款被疯子道破", "#素描预言画", "#李青鸟预言另一个世界",
                    "#北俱芦洲偷渡名额", "#归零老人控场", "#反向驯化精神病人",
                    "#亲戚入驻豪宅扔遗像", "#全家贪婪嘴脸曝光", "#绝不原谅绝地反杀"
                ]
            },
            "compliance_risk": {
                "risk_level": "擦边预警",
                "risk_reason": "含精神病院限制人身自由、假诊断等情节，需弱化制度对抗，强化民事夺产与正义反杀"
            }
        }
    },
    {
        "meta": {
            "source_platform": "起点中文网",
            "rank_name": "畅销榜",
            "rank_position": 2,
            "board_type": "MONTHLY_HOT",
            "days_on_rank": 8
        },
        "profile": {
            "title": "夜无疆",
            "author": "辰东",
            "track_and_audience": {
                "gender_track": "男频",
                "main_category": "玄幻",
                "sub_category": "永夜末世玄幻/黑暗修仙逆袭",
                "target_audience": "男频玄幻核心读者、15-35岁男性网文受众、下沉市场中青年短剧用户"
            },
            "tone_and_intensity": {
                "narrative_pace": "极致快节奏",
                "emotional_tone": "悲壮热血与黑暗压抑交织，强情绪反杀与极致爽快"
            },
            "identity_contrast": {
                "surface_identity": "在永夜降临中被家族视为废体、遭献祭抛弃的底层少年/罪血后裔",
                "hidden_identity": "唯一能在永夜中点燃命灯的不灭者、上古禁忌传承者、黑暗纪元的天命之主",
                "contrast_gap": "从被献祭弃子到执掌永夜纪元的唯一光明，身份反差极致，由举世皆敌到举世跪伏"
            },
            "opening_crisis_hook": {
                "conflict_type": "永夜灭世/家族献祭/被夺血脉/逐出家门",
                "crisis_description": "永夜骤然降临，城池沦陷，家族为求自保将主角作为祭品推入黑暗，夺其命灯、断其生路，主角在万鬼噬身中觉醒不灭灯火。"
            },
            "payoff_mode": {
                "leverage": "神秘古灯/不灭命灯/禁忌功法/永夜唯一通行体质/认知差",
                "core_payoff": "打脸虐渣、逆袭反杀、拯救苍生、独断万古、绝不原谅"
            },
            "hierarchical_tropes": {
                "l1_macro_tropes": ["#永夜末世", "#逆天改命", "#独断万古", "#黑暗崛起", "#守护苍生"],
                "l2_micro_elements": [
                    "#永夜降临", "#献祭亲子", "#命灯觉醒", "#断亲逐出",
                    "#抢夺血脉", "#古碑传承", "#万鬼噬身", "#点燃不灭灯火",
                    "#禁区归来", "#横推敌手", "#举世皆敌", "#全家悔恨跪求"
                ]
            },
            "compliance_risk": {
                "risk_level": "擦边预警",
                "risk_reason": "包含永夜灭世、献祭、万鬼噬身等黑暗恐怖意象，需弱化封建迷信渲染"
            }
        }
    },
    {
        "meta": {
            "source_platform": "晋江文学城",
            "rank_name": "读者栽培榜",
            "rank_position": 1,
            "board_type": "RETENTION_HOT",
            "days_on_rank": 5
        },
        "profile": {
            "title": "嫡嫁千金",
            "author": "千山茶客",
            "track_and_audience": {
                "gender_track": "女频",
                "main_category": "古言",
                "sub_category": "古言重生宅斗复仇/嫡女替嫁逆袭",
                "target_audience": "18-35岁女性，偏好宅斗、重生、复仇、权谋、先虐后爽的古言受众"
            },
            "tone_and_intensity": {
                "narrative_pace": "快节奏，强冲突密集，每集设置反转与打脸节点",
                "emotional_tone": "强情绪反杀，虐渣打脸，爽虐交织，绝不原谅"
            },
            "identity_contrast": {
                "surface_identity": "被继母庶妹陷害、遭家族厌弃、被迫替嫁冲喜的落魄嫡女",
                "hidden_identity": "重生归来的嫡女，身怀前世记忆与权谋手腕，背后有顶级世家势力，终成掌权者",
                "contrast_gap": "表面任人宰割的弃女，实则是步步为营的重生复仇者，身份与手段反差极强"
            },
            "opening_crisis_hook": {
                "conflict_type": "替嫁冲喜/被退婚/继母庶妹陷害/抢夺母亲遗物",
                "crisis_description": "重生睁眼回到被继母庶妹陷害的订婚宴，母亲遗物被夺，婚约被抢，还被逼替嫁给传闻暴戾将死的王爷冲喜，女主绝地反击。"
            },
            "payoff_mode": {
                "leverage": "重生先知信息差+宅斗权谋手腕+隐藏顶级世家势力",
                "core_payoff": "打脸虐渣、权谋反杀、全家悔恨、绝不原谅，兼有夫君追妻火葬场"
            },
            "hierarchical_tropes": {
                "l1_macro_tropes": ["#嫡女重生", "#宅斗复仇", "#替嫁冲喜", "#打脸虐渣", "#权谋大女主"],
                "l2_micro_elements": [
                    "#雨夜罚跪", "#母亲遗物被夺", "#被逼替嫁病王爷", "#庶妹抢婚约",
                    "#继母伪善陷害", "#灵堂反杀", "#撕毁婚书", "#掌掴庶妹",
                    "#外祖家撑腰", "#夫君病中相护", "#全家悔恨下跪", "#绝不原谅"
                ]
            },
            "compliance_risk": {
                "risk_level": "安全",
                "risk_reason": "合规安全"
            }
        }
    }
]


def seed_database():
    """执行数据库建表与基准数据写入"""
    print("=" * 72)
    print("  PostgreSQL 工业级数据库初始化与基准小说 DNA 基因库写入")
    print("=" * 72)
    storage = PgNovelStorage()
    
    # 1. 执行 DDL
    ok = storage.init_database()
    if not ok:
        print("[ERROR] 数据库初始化失败，请检查网络或配置")
        return False

    # 2. 写入 5 大标杆网文
    print("\n[数据写入] 正在同步写入 5 大标杆网文大盘基因...")
    for item in BENCHMARK_NOVELS:
        meta = item["meta"]
        prof = item["profile"]
        success = storage.upsert_novel_dna(
            profile=prof,
            source_platform=meta["source_platform"],
            rank_name=meta["rank_name"],
            rank_position=meta["rank_position"],
            board_type=meta["board_type"],
            days_on_rank=meta["days_on_rank"]
        )
        print(f"  -> 《{prof['title']}》（{prof['author']}）[{meta['source_platform']} - {meta['rank_name']} 第{meta['rank_position']}名]: {'成功写入' if success else '写入失败'}")

    # 3. 统计结果与 GIN 倒排索引验证
    count = storage.get_count()
    print(f"\n[验证完成] 数据库当前有效大盘小说总量: {count} 部")

    # 4. 实测 GIN 倒排索引查询能力
    print("\n[GIN 倒排索引实测] 检索母题 ['#真假千金', '#全能马甲']:")
    matched = storage.search_by_tropes(["#真假千金", "#全能马甲"], channel="女频")
    for m in matched:
        print(f"  -> 微秒级命中: 《{m['title']}》({m['author']}) | 平台: {m['source_platform']} | 热度分: {m['heat_score']} | 状态: {m['lifecycle_stage']}")

    return True


if __name__ == "__main__":
    seed_database()
