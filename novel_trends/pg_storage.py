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
        """获取 psycopg2 数据库连接（带断连重试，规避远端偶发断连）"""
        import time

        last_error = None
        for attempt in range(3):
            try:
                return psycopg2.connect(
                    host=self.config["PG_HOST"],
                    port=int(self.config["PG_PORT"]),
                    dbname=self.config["PG_DATABASE"],
                    user=self.config["PG_USER"],
                    password=self.config["PG_PASSWORD"],
                    connect_timeout=10,
                )
            except psycopg2.OperationalError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise last_error

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
            lifecycle_stage VARCHAR(16) DEFAULT 'SUPER_EXPLODING',        -- EXPLODING (0~10天) / MATURE (11~30天) / SATURATED (31~60天) / DEPRECATED (>60天)

            -- 4. 赛道与受众标量 (B-Tree 索引)
            gender_track VARCHAR(16) NOT NULL,                      -- 男频 / 女频
            main_category VARCHAR(32) NOT NULL,                     -- 主题材 (都市 / 现言 / 古言 / 玄幻 / 悬疑)
            sub_category VARCHAR(64) NOT NULL,                      -- 细分赛道 (真假千金马甲 / 战神出狱 / 精神病院怪谈)
            target_audience VARCHAR(128) DEFAULT '',                -- 受众画像
            worldview_setting VARCHAR(64) DEFAULT '',               -- 【维度2】世界观基底
            core_sub_genre VARCHAR(64) DEFAULT '',                  -- 【维度2】核心细分流派
            leverage_category VARCHAR(32) DEFAULT '',               -- 【维度3】破局底牌6大分类
            first_counterattack_payoff TEXT DEFAULT '',             -- 【维度4】第一集首次反击爽点事件
            extraction_confidence VARCHAR(16) DEFAULT '高',         -- 【维度1】大模型提取把握度
            
            -- 5. 两级分层母题数组 (GIN 倒排索引极速比对)
            l1_macro_tropes TEXT[] NOT NULL DEFAULT '{}',           -- L1 宏观母题 (3~5个)
            l2_micro_elements TEXT[] NOT NULL DEFAULT '{}',         -- L2 微观剧情元素 (8~12个)
            
            -- 6. 完整 6 维结构化分析 JSONB
            dna_profile JSONB NOT NULL,
            
            -- 7. 业务状态
            status VARCHAR(16) DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- 建立高性能索引
        CREATE INDEX IF NOT EXISTS idx_novel_dna_track 
            ON novel_dna_records (gender_track, main_category);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_sub 
            ON novel_dna_records (sub_category);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_leverage_cat 
            ON novel_dna_records (leverage_category);

        CREATE INDEX IF NOT EXISTS idx_novel_dna_core_sub_genre 
            ON novel_dna_records (core_sub_genre);

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
                    cur.execute("""
                        ALTER TABLE crawled_ranking_books ADD COLUMN IF NOT EXISTS first_crawled_at TIMESTAMPTZ DEFAULT NOW();
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS first_ranked_at DATE DEFAULT CURRENT_DATE;
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS last_ranked_at DATE DEFAULT CURRENT_DATE;
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS worldview_setting VARCHAR(64) DEFAULT '';
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS core_sub_genre VARCHAR(64) DEFAULT '';
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS leverage_category VARCHAR(32) DEFAULT '';
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS first_counterattack_payoff TEXT DEFAULT '';
                        ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS extraction_confidence VARCHAR(16) DEFAULT '高';

                        CREATE INDEX IF NOT EXISTS idx_novel_dna_leverage_cat ON novel_dna_records (leverage_category);
                        CREATE INDEX IF NOT EXISTS idx_novel_dna_core_sub_genre ON novel_dna_records (core_sub_genre);
                    """)
                conn.commit()
            print("[PgNovelStorage] PostgreSQL 表结构与 GIN 倒排索引初始化成功！")
            # 自动注入全量字段与表结构中文备注
            self.apply_database_comments()
            return True
        except Exception as e:
            print(f"[PgNovelStorage] 初始化表结构失败: {e}")
            return False

    def apply_database_comments(self) -> bool:
        """
        为 PostgreSQL 中的 crawled_ranking_books 和 novel_dna_records 表及其所有字段
        注入标准的中文注释 (COMMENT ON TABLE / COLUMN)，支持在 Navicat/DBeaver 中清晰可视化
        """
        comments_sql = """
        -- 表 1: crawled_ranking_books (全网实时在榜小说作品明细流水表)
        COMMENT ON TABLE crawled_ranking_books IS '全网真实在榜小说作品实时明细表（四大平台14大快节奏榜单流水数据）';
        COMMENT ON COLUMN crawled_ranking_books.id IS '自增唯一主键 ID';
        COMMENT ON COLUMN crawled_ranking_books.book_id IS '原生平台书籍物理唯一ID（如起点1042108416，晋江10841540）';
        COMMENT ON COLUMN crawled_ranking_books.title IS '作品标准书名（已清洗章节与非法前缀）';
        COMMENT ON COLUMN crawled_ranking_books.author IS '真实作者笔名（100%全网创作者笔名，严禁占位符）';
        COMMENT ON COLUMN crawled_ranking_books.platform IS '数据源平台（起点中文网 / 纵横中文网 / 晋江文学城 / 潇湘书院）';
        COMMENT ON COLUMN crawled_ranking_books.channel IS '受众频道赛道（男频 / 女频）';
        COMMENT ON COLUMN crawled_ranking_books.rank_name IS '所处快节奏榜单名称（月票榜 / 新晋作者榜 / 阅读榜 / 读者栽培榜等）';
        COMMENT ON COLUMN crawled_ranking_books.rank_position IS '在榜名次排位（1~50 或 1~20）';
        COMMENT ON COLUMN crawled_ranking_books.category IS '细分题材分类与核心风格标签';
        COMMENT ON COLUMN crawled_ranking_books.intro IS '官方文案简介/核心人设与剧情梗概';
        COMMENT ON COLUMN crawled_ranking_books.book_url IS '作品在原生平台的落地页官方URL';
        COMMENT ON COLUMN crawled_ranking_books.heat_score IS '基于位次与榜单权重映射的大盘基础热度赋分（60.00~100.00）';
        COMMENT ON COLUMN crawled_ranking_books.first_crawled_at IS '作品在该榜位首次抓取入库时间';
        COMMENT ON COLUMN crawled_ranking_books.fetched_at IS '爬虫抓取落盘或Upsert更新时间戳';

        -- 表 2: novel_dna_records (大盘网文 DNA 深度特征基因库表)
        COMMENT ON TABLE novel_dna_records IS '大盘网文DNA 6大黄金维度深度特征基因库表（供短剧初筛加权打分匹配）';
        COMMENT ON COLUMN novel_dna_records.id IS '自增唯一主键 ID';
        COMMENT ON COLUMN novel_dna_records.book_key IS '唯一业务主键（{书名}_{作者}，防重保证）';
        COMMENT ON COLUMN novel_dna_records.title IS '作品名称';
        COMMENT ON COLUMN novel_dna_records.author IS '真实作者笔名';
        COMMENT ON COLUMN novel_dna_records.source_platform IS '数据来源平台（起点/纵横/晋江/潇湘/番茄等）';
        COMMENT ON COLUMN novel_dna_records.rank_name IS '所在核心榜单名称';
        COMMENT ON COLUMN novel_dna_records.rank_position IS '榜单位次（1~100）';
        COMMENT ON COLUMN novel_dna_records.board_type IS '榜单业务类型（NEW_TREND新晋潜力 / MONTHLY_HOT月度主力 / RETENTION_HOT高留存追读）';
        COMMENT ON COLUMN novel_dna_records.heat_score IS '经时效生命周期衰减后的有效大盘热度分（20.00~110.00）';
        COMMENT ON COLUMN novel_dna_records.first_ranked_at IS '首次在榜探测日期';
        COMMENT ON COLUMN novel_dna_records.last_ranked_at IS '最近一次探测在榜日期';
        COMMENT ON COLUMN novel_dna_records.days_on_rank IS '累计在榜天数（生命周期衰减核心输入变量）';
        COMMENT ON COLUMN novel_dna_records.is_currently_on_rank IS '当前是否在榜活跃（TRUE活跃 / FALSE跌出榜单）';
        COMMENT ON COLUMN novel_dna_records.lifecycle_stage IS '极速AI短剧时效生命周期阶段（SUPER_EXPLODING极速爆发 / PEAK_HARVEST黄金收割 / SATURATED饱和内卷 / OVERDUE超期长尾 / DROPPED跌出榜单）';
        COMMENT ON COLUMN novel_dna_records.gender_track IS '【维度2】受众赛道（男频 / 女频 / 泛性别，第一层漏斗粗筛红线）';
        COMMENT ON COLUMN novel_dna_records.worldview_setting IS '【维度2】世界观基底（现代都市 / 末日天灾 / 规则怪谈 / 玄幻修仙 / 古代架空 / 脑洞变异 等）';
        COMMENT ON COLUMN novel_dna_records.core_sub_genre IS '【维度2】核心细分流派（末世囤货安全屋 / 真假千金断亲 / 全家偷听心声 / 神豪返利系统 等）';
        COMMENT ON COLUMN novel_dna_records.leverage_category IS '【维度3】破局底牌6大分类（系统面板类 / 重生先知类 / 异能空间类 / 隐藏马甲类 / 契约羁绊类 / 极致智商类）';
        COMMENT ON COLUMN novel_dna_records.first_counterattack_payoff IS '【维度4】第一集首次反击爽点事件描述（第一集剧情骨架比对）';
        COMMENT ON COLUMN novel_dna_records.extraction_confidence IS '【维度1】大模型提取把握度（高 / 中 / 低，若正文缺失或低把握度自动洗掉丢弃）';
        COMMENT ON COLUMN novel_dna_records.main_category IS '【兼容列】主题材/世界观基底（对齐 worldview_setting）';
        COMMENT ON COLUMN novel_dna_records.sub_category IS '【兼容列】核心细分赛道流派（对齐 core_sub_genre）';
        COMMENT ON COLUMN novel_dna_records.target_audience IS '【兼容列】开局弱势受迫害状态描述（surface_status）';
        COMMENT ON COLUMN novel_dna_records.l1_macro_tropes IS '【维度6】L1宏观母题标签数组（带#号，GIN倒排索引，微秒级重叠比对）';
        COMMENT ON COLUMN novel_dna_records.l2_micro_elements IS '【维度6】L2微观剧情名场面与看点标签数组（带#号，GIN倒排索引，近期风向标加权）';
        COMMENT ON COLUMN novel_dna_records.dna_profile IS '【完整6维结构化深度基因分析JSONB】包含：1.数据质检过滤 2.赛道世界观 3.主角人设与破局底牌 4.开局冲突反杀 5.第一集音画匹配锚点 6.二级爆款标签树';
        COMMENT ON COLUMN novel_dna_records.status IS '记录业务状态（active有效参与加权 / archived冷备归档）';
        COMMENT ON COLUMN novel_dna_records.created_at IS '基因特征首次分析建档时间戳';
        COMMENT ON COLUMN novel_dna_records.updated_at IS '基因或生命周期衰减最近一次更新时间戳';

        -- 表 3: novel_chapters (网络文学榜单作品章节正文与目录表)
        COMMENT ON TABLE novel_chapters IS '网络文学榜单作品章节正文与目录流水表';
        COMMENT ON COLUMN novel_chapters.id IS '自增唯一主键 ID';
        COMMENT ON COLUMN novel_chapters.platform IS '来源平台（起点/纵横/晋江/潇湘等）';
        COMMENT ON COLUMN novel_chapters.book_id IS '原生平台书籍物理唯一 ID';
        COMMENT ON COLUMN novel_chapters.chapter_index IS '章节序号（从 1 开始按序排布）';
        COMMENT ON COLUMN novel_chapters.chapter_id IS '平台原生章节唯一编号';
        COMMENT ON COLUMN novel_chapters.title IS '章节标题名称';
        COMMENT ON COLUMN novel_chapters.content IS '正文纯文本内容';
        COMMENT ON COLUMN novel_chapters.is_free IS '是否免费公开章节（TRUE免费 / FALSE付费）';
        COMMENT ON COLUMN novel_chapters.word_count IS '章节纯文本有效字数';
        COMMENT ON COLUMN novel_chapters.url IS '章节原生阅读落地页 URL';
        COMMENT ON COLUMN novel_chapters.crawled_at IS '爬虫抓取落库时间戳';

        -- 表 4: novel_intros (网络文学作品官方简介详情表)
        COMMENT ON TABLE novel_intros IS '网络文学作品官方简介详情表';
        COMMENT ON COLUMN novel_intros.id IS '自增唯一主键 ID';
        COMMENT ON COLUMN novel_intros.platform IS '来源平台';
        COMMENT ON COLUMN novel_intros.book_id IS '平台原生书籍 ID';
        COMMENT ON COLUMN novel_intros.title IS '作品标准书名';
        COMMENT ON COLUMN novel_intros.author IS '作者笔名';
        COMMENT ON COLUMN novel_intros.intro IS '官方文案简介与核心人设剧情梗概';
        COMMENT ON COLUMN novel_intros.url IS '书籍原生详情页 URL';
        COMMENT ON COLUMN novel_intros.crawled_at IS '简介抓取落库时间戳';

        -- 表 5: novel_content_status (小说正文抓取进度与完整度状态表)
        COMMENT ON TABLE novel_content_status IS '小说正文抓取进度与完整度状态监控表';
        COMMENT ON COLUMN novel_content_status.platform IS '来源平台';
        COMMENT ON COLUMN novel_content_status.book_id IS '平台原生书籍 ID';
        COMMENT ON COLUMN novel_content_status.target_chapters IS '目标计划抓取章节总数';
        COMMENT ON COLUMN novel_content_status.content_chapters IS '已抓取并成功落库正文的章节数';
        COMMENT ON COLUMN novel_content_status.title_chapters IS '已解析目录标题的章节数';
        COMMENT ON COLUMN novel_content_status.exhausted IS '是否已抓取完该书所有可用章节';
        COMMENT ON COLUMN novel_content_status.updated_at IS '状态最近一次更新时间戳';
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(comments_sql)
                conn.commit()
            print("[PgNovelStorage] PostgreSQL 表及全量字段中文注释 (COMMENT ON) 应用成功！")
            return True
        except Exception as e:
            print(f"[PgNovelStorage] 应用表及字段注释失败: {e}")
            return False

    @staticmethod
    def calculate_heat_score(rank_position: int, days_on_rank: int = 1, is_currently_on_rank: bool = True) -> Tuple[float, str]:
        """
        根据榜单名次、在榜天数与在榜状态，按【极速 AI 短剧/漫剧时效模型】计算大盘热度与生命周期阶段：
        - 跌出榜单 (not on rank): 跌榜淘汰期 (DROPPED)，深度衰减 0.35x
        - 1 ~ 3 天: 极速爆发期 (SUPER_EXPLODING)，首发红利加权 1.25x
        - 4 ~ 7 天: 黄金收割期 (PEAK_HARVEST)，顶峰跑量加权 1.15x
        - 8 ~ 20 天: 饱和内卷期 (SATURATED)，同质化疲劳衰减 0.80x
        - > 20 天: 超期过气期 (OVERDUE)，长尾疲劳衰减 0.60x
        """
        pos = max(1, rank_position)
        # 基础名次分: 100 - 15 * log10(pos)
        base_score = max(60.0, 100.0 - 15.0 * math.log10(pos))

        if not is_currently_on_rank:
            stage = "DROPPED"
            decay_factor = 0.35
        elif days_on_rank <= 3:
            stage = "SUPER_EXPLODING"
            decay_factor = 1.25
        elif days_on_rank <= 7:
            stage = "PEAK_HARVEST"
            decay_factor = 1.15
        elif days_on_rank <= 20:
            stage = "SATURATED"
            decay_factor = 0.80
        else:
            stage = "OVERDUE"
            decay_factor = 0.60

        final_score = min(100.0, round(base_score * decay_factor, 2))
        return final_score, stage

    def upsert_novel_dna(
        self,
        profile: Dict[str, Any],
        source_platform: str = "番茄小说",
        rank_name: str = "热读榜",
        rank_position: int = 1,
        board_type: str = "NEW_TREND",
        days_on_rank: Optional[int] = None,
        force_days: bool = False
    ) -> bool:
        """写入或更新单部小说的 DNA 记录 (幂等 Upsert，支持在榜天数智能跨天累加)"""
        title = profile.get("title", "").strip()
        author = profile.get("author", "").strip()
        book_key = f"{title}_{author}"

        initial_days = days_on_rank if (days_on_rank is not None and days_on_rank > 0) else 1
        heat_score, lifecycle_stage = self.calculate_heat_score(rank_position, initial_days)

        track_info = profile.get("track_dna") or profile.get("track_and_audience", {})
        gender_track = track_info.get("gender_track", "男频")
        main_category = track_info.get("worldview_setting") or track_info.get("main_category", "都市")
        sub_category = track_info.get("core_sub_genre") or track_info.get("sub_category", "通用")
        protagonist = (
            profile.get("protagonist_and_leverage_dna")
            or profile.get("protagonist_and_cheat_dna", {})
            or profile.get("identity_contrast", {})
        )
        target_audience = protagonist.get("surface_status", "") or track_info.get("target_audience", "")

        worldview_setting = track_info.get("worldview_setting") or main_category
        core_sub_genre = track_info.get("core_sub_genre") or sub_category
        leverage_category = protagonist.get("leverage_category") or protagonist.get("cheat_mechanism_type", "")
        conflict = profile.get("conflict_and_payoff_dna") or profile.get("opening_crisis_hook", {})
        first_payoff = conflict.get("first_counterattack_payoff") or conflict.get("crisis_description", "")
        dq = profile.get("data_quality_check", {})
        confidence = str(dq.get("extraction_confidence", "高")).strip()

        tropes = profile.get("hierarchical_tropes", {})
        l1_tropes = tropes.get("l1_macro_tropes", [])
        l2_tropes = tropes.get("l2_micro_elements", [])

        if force_days:
            sql = """
            INSERT INTO novel_dna_records (
                book_key, title, author,
                source_platform, rank_name, rank_position, board_type,
                heat_score, days_on_rank, lifecycle_stage,
                first_ranked_at, last_ranked_at, is_currently_on_rank,
                gender_track, main_category, sub_category, target_audience,
                worldview_setting, core_sub_genre, leverage_category,
                first_counterattack_payoff, extraction_confidence,
                l1_macro_tropes, l2_micro_elements,
                dna_profile, updated_at
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                CURRENT_DATE, CURRENT_DATE, TRUE,
                %s, %s, %s, %s,
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
                days_on_rank = EXCLUDED.days_on_rank,
                lifecycle_stage = EXCLUDED.lifecycle_stage,
                last_ranked_at = CURRENT_DATE,
                is_currently_on_rank = TRUE,
                gender_track = EXCLUDED.gender_track,
                main_category = EXCLUDED.main_category,
                sub_category = EXCLUDED.sub_category,
                target_audience = EXCLUDED.target_audience,
                worldview_setting = EXCLUDED.worldview_setting,
                core_sub_genre = EXCLUDED.core_sub_genre,
                leverage_category = EXCLUDED.leverage_category,
                first_counterattack_payoff = EXCLUDED.first_counterattack_payoff,
                extraction_confidence = EXCLUDED.extraction_confidence,
                l1_macro_tropes = EXCLUDED.l1_macro_tropes,
                l2_micro_elements = EXCLUDED.l2_micro_elements,
                dna_profile = EXCLUDED.dna_profile,
                updated_at = NOW();
            """
        else:
            sql = """
            INSERT INTO novel_dna_records (
                book_key, title, author,
                source_platform, rank_name, rank_position, board_type,
                heat_score, days_on_rank, lifecycle_stage,
                first_ranked_at, last_ranked_at, is_currently_on_rank,
                gender_track, main_category, sub_category, target_audience,
                worldview_setting, core_sub_genre, leverage_category,
                first_counterattack_payoff, extraction_confidence,
                l1_macro_tropes, l2_micro_elements,
                dna_profile, updated_at
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                CURRENT_DATE, CURRENT_DATE, TRUE,
                %s, %s, %s, %s,
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
                days_on_rank = CASE 
                    WHEN novel_dna_records.last_ranked_at < CURRENT_DATE 
                    THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at)
                    ELSE novel_dna_records.days_on_rank 
                END,
                last_ranked_at = CURRENT_DATE,
                is_currently_on_rank = TRUE,
                lifecycle_stage = CASE 
                    WHEN (CASE WHEN novel_dna_records.last_ranked_at < CURRENT_DATE THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at) ELSE novel_dna_records.days_on_rank END) <= 3 THEN 'SUPER_EXPLODING'
                    WHEN (CASE WHEN novel_dna_records.last_ranked_at < CURRENT_DATE THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at) ELSE novel_dna_records.days_on_rank END) <= 7 THEN 'PEAK_HARVEST'
                    WHEN (CASE WHEN novel_dna_records.last_ranked_at < CURRENT_DATE THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at) ELSE novel_dna_records.days_on_rank END) <= 20 THEN 'SATURATED'
                    ELSE 'OVERDUE'
                END,
                heat_score = LEAST(100.0, ROUND((
                    GREATEST(60.0, 100.0 - 15.0 * LOG(GREATEST(1, EXCLUDED.rank_position))) *
                    CASE 
                        WHEN (CASE WHEN novel_dna_records.last_ranked_at < CURRENT_DATE THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at) ELSE novel_dna_records.days_on_rank END) <= 3 THEN 1.25
                        WHEN (CASE WHEN novel_dna_records.last_ranked_at < CURRENT_DATE THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at) ELSE novel_dna_records.days_on_rank END) <= 7 THEN 1.15
                        WHEN (CASE WHEN novel_dna_records.last_ranked_at < CURRENT_DATE THEN novel_dna_records.days_on_rank + (CURRENT_DATE - novel_dna_records.last_ranked_at) ELSE novel_dna_records.days_on_rank END) <= 20 THEN 0.80
                        ELSE 0.60
                    END
                )::numeric, 2)),
                gender_track = EXCLUDED.gender_track,
                main_category = EXCLUDED.main_category,
                sub_category = EXCLUDED.sub_category,
                target_audience = EXCLUDED.target_audience,
                worldview_setting = EXCLUDED.worldview_setting,
                core_sub_genre = EXCLUDED.core_sub_genre,
                leverage_category = EXCLUDED.leverage_category,
                first_counterattack_payoff = EXCLUDED.first_counterattack_payoff,
                extraction_confidence = EXCLUDED.extraction_confidence,
                l1_macro_tropes = EXCLUDED.l1_macro_tropes,
                l2_micro_elements = EXCLUDED.l2_micro_elements,
                dna_profile = EXCLUDED.dna_profile,
                updated_at = NOW();
            """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        book_key, title, author,
                        source_platform, rank_name, rank_position, board_type,
                        heat_score, initial_days, lifecycle_stage,
                        gender_track, main_category, sub_category, target_audience,
                        worldview_setting, core_sub_genre, leverage_category,
                        first_payoff, confidence,
                        l1_tropes, l2_tropes,
                        json.dumps(profile, ensure_ascii=False)
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
        支持自动维护作品首次抓取时间 first_crawled_at
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
            first_crawled_at TIMESTAMPTZ DEFAULT NOW(),
            fetched_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_platform_rank_pos UNIQUE (platform, rank_name, rank_position)
        );
        ALTER TABLE crawled_ranking_books ADD COLUMN IF NOT EXISTS first_crawled_at TIMESTAMPTZ DEFAULT NOW();
        CREATE INDEX IF NOT EXISTS idx_crawled_platform ON crawled_ranking_books(platform, rank_name);
        CREATE INDEX IF NOT EXISTS idx_crawled_title ON crawled_ranking_books(title, author);
        """
        sql = """
        INSERT INTO crawled_ranking_books (
            book_id, title, author, platform, channel,
            rank_name, rank_position, category, intro, book_url,
            heat_score, first_crawled_at, fetched_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, NOW(), NOW()
        )
        ON CONFLICT (platform, rank_name, rank_position) DO UPDATE SET
            book_id = EXCLUDED.book_id,
            first_crawled_at = CASE 
                WHEN crawled_ranking_books.title != EXCLUDED.title OR crawled_ranking_books.author != EXCLUDED.author 
                THEN NOW() 
                ELSE COALESCE(crawled_ranking_books.first_crawled_at, crawled_ranking_books.fetched_at, NOW())
            END,
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
            # 自动刷新跌出榜单书籍的生命周期与衰减状态 (0.35x)
            dropped_cnt = self.refresh_dropped_books_status()
            if dropped_cnt > 0:
                print(f"[PgNovelStorage] 探测到 {dropped_cnt} 部作品已跌出各大榜单，已自动降权至 DROPPED (0.35x)")
            return saved
        except Exception as e:
            print(f"[PgNovelStorage] 批量写入抓取书籍失败: {e}")
            return 0

    def refresh_dropped_books_status(self) -> int:
        """
        比对当前 crawled_ranking_books 实时榜单席位，
        将已经跌出全网四大平台各大榜单的作品自动标记为 is_currently_on_rank = FALSE，
        时效阶段流转为 DROPPED (跌榜淘汰期)，热度深度衰减为 0.35x
        """
        sql = """
        UPDATE novel_dna_records d
        SET is_currently_on_rank = FALSE,
            lifecycle_stage = 'DROPPED',
            heat_score = LEAST(100.0, ROUND((
                GREATEST(60.0, 100.0 - 15.0 * LOG(GREATEST(1, d.rank_position))) * 0.35
            )::numeric, 2)),
            updated_at = NOW()
        WHERE d.is_currently_on_rank = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM crawled_ranking_books c
              WHERE c.title = d.title AND c.author = d.author
          );
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    updated = cur.rowcount
                conn.commit()
            return updated
        except Exception as e:
            print(f"[PgNovelStorage] 刷新跌榜作品状态异常: {e}")
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

    # ------------------------------------------------------------------
    # 章节正文持久化
    # ------------------------------------------------------------------
    def init_content_tables(self) -> bool:
        """创建章节表及索引（幂等）"""
        ddl = """
        CREATE TABLE IF NOT EXISTS novel_chapters (
            id BIGSERIAL PRIMARY KEY,
            platform VARCHAR(32) NOT NULL,
            book_id VARCHAR(64) NOT NULL,
            chapter_index INT NOT NULL,
            chapter_id VARCHAR(64) DEFAULT '',
            title VARCHAR(256) DEFAULT '',
            content TEXT DEFAULT '',
            is_free BOOLEAN DEFAULT TRUE,
            word_count INT DEFAULT 0,
            url TEXT DEFAULT '',
            crawled_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_novel_chapter UNIQUE (platform, book_id, chapter_index)
        );
        CREATE INDEX IF NOT EXISTS idx_chapters_book ON novel_chapters(platform, book_id);

        CREATE TABLE IF NOT EXISTS novel_intros (
            id BIGSERIAL PRIMARY KEY,
            platform VARCHAR(32) NOT NULL,
            book_id VARCHAR(64) NOT NULL,
            title VARCHAR(256) DEFAULT '',
            author VARCHAR(128) DEFAULT '',
            intro TEXT DEFAULT '',
            url TEXT DEFAULT '',
            crawled_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_novel_intro UNIQUE (platform, book_id)
        );
        CREATE INDEX IF NOT EXISTS idx_intros_book ON novel_intros(platform, book_id);

        CREATE TABLE IF NOT EXISTS novel_content_status (
            platform VARCHAR(32) NOT NULL,
            book_id VARCHAR(64) NOT NULL,
            target_chapters INT DEFAULT 0,
            content_chapters INT DEFAULT 0,
            title_chapters INT DEFAULT 0,
            exhausted BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (platform, book_id)
        );
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(ddl)
                conn.commit()
            return True
        except Exception as e:
            print(f"[PgNovelStorage] 初始化章节/简介表失败: {e}")
            return False

    def save_chapters(self, chapters: List[Any]) -> int:
        """批量 Upsert 章节（标题与正文）"""
        if not chapters:
            return 0
        sql = """
        INSERT INTO novel_chapters (
            platform, book_id, chapter_index, chapter_id, title,
            content, is_free, word_count, url, crawled_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (platform, book_id, chapter_index) DO UPDATE SET
            chapter_id = EXCLUDED.chapter_id,
            title = EXCLUDED.title,
            content = CASE WHEN EXCLUDED.content <> '' THEN EXCLUDED.content ELSE novel_chapters.content END,
            is_free = EXCLUDED.is_free,
            word_count = GREATEST(novel_chapters.word_count, EXCLUDED.word_count),
            url = EXCLUDED.url,
            crawled_at = NOW();
        """
        saved = 0
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    for ch in chapters:
                        cur.execute(sql, (
                            getattr(ch, "platform", ""),
                            getattr(ch, "book_id", ""),
                            int(getattr(ch, "chapter_index", 0) or 0),
                            getattr(ch, "chapter_id", "") or "",
                            getattr(ch, "title", "") or "",
                            getattr(ch, "content", "") or "",
                            bool(getattr(ch, "is_free", True)),
                            int(getattr(ch, "word_count", 0) or 0),
                            getattr(ch, "url", "") or "",
                        ))
                        saved += 1
                conn.commit()
            return saved
        except Exception as e:
            print(f"[PgNovelStorage] 写入章节失败: {e}")
            return 0

    def save_intros(self, intros: List[Any]) -> int:
        """批量 Upsert 书籍简介"""
        if not intros:
            return 0
        sql = """
        INSERT INTO novel_intros (platform, book_id, title, author, intro, url, crawled_at)
        VALUES (%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (platform, book_id) DO UPDATE SET
            title = EXCLUDED.title,
            author = EXCLUDED.author,
            intro = CASE WHEN EXCLUDED.intro <> '' THEN EXCLUDED.intro ELSE novel_intros.intro END,
            url = EXCLUDED.url,
            crawled_at = NOW();
        """
        saved = 0
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    for it in intros:
                        intro = (getattr(it, "intro", "") or "").strip()
                        if not intro:
                            continue
                        cur.execute(sql, (
                            getattr(it, "platform", ""),
                            getattr(it, "book_id", ""),
                            getattr(it, "title", "") or "",
                            getattr(it, "author", "") or "",
                            intro,
                            getattr(it, "url", "") or "",
                        ))
                        saved += 1
                conn.commit()
            return saved
        except Exception as e:
            print(f"[PgNovelStorage] 写入简介失败: {e}")
            return 0

    def save_content_status(
        self,
        platform: str,
        book_id: str,
        target_chapters: int,
        content_chapters: int,
        title_chapters: int,
        exhausted: bool,
    ) -> bool:
        """记录单本书的内容采集状态，供「补抓缺失/不足章节」判断。"""
        sql = """
        INSERT INTO novel_content_status (
            platform, book_id, target_chapters, content_chapters,
            title_chapters, exhausted, updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (platform, book_id) DO UPDATE SET
            target_chapters = EXCLUDED.target_chapters,
            content_chapters = EXCLUDED.content_chapters,
            title_chapters = EXCLUDED.title_chapters,
            exhausted = EXCLUDED.exhausted,
            updated_at = NOW();
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        platform, book_id, int(target_chapters),
                        int(content_chapters), int(title_chapters), bool(exhausted),
                    ))
                conn.commit()
            return True
        except Exception as e:
            print(f"[PgNovelStorage] 写入内容采集状态失败: {e}")
            return False

    def get_chapter_count(self) -> int:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM novel_chapters;")
                    return cur.fetchone()[0]
        except Exception:
            return 0

    def get_intro_count(self) -> int:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM novel_intros;")
                    return cur.fetchone()[0]
        except Exception:
            return 0

    def count_missing_chapters(self, target_chapters: int = 10) -> int:
        """统计仍需补抓章节的书：正文数 < 目标，且未被判定为「已抓完」。"""
        sql = """
        WITH books AS (
            SELECT DISTINCT platform, book_id
            FROM crawled_ranking_books
            WHERE COALESCE(book_id, '') <> ''
        ),
        agg AS (
            SELECT platform, book_id,
                   COUNT(*) FILTER (WHERE content <> '') AS content_chapters
            FROM novel_chapters
            GROUP BY platform, book_id
        )
        SELECT COUNT(*)
        FROM books b
        LEFT JOIN agg a ON a.platform = b.platform AND a.book_id = b.book_id
        LEFT JOIN novel_content_status s ON s.platform = b.platform AND s.book_id = b.book_id
        WHERE COALESCE(a.content_chapters, 0) < %s
          AND COALESCE(s.exhausted, FALSE) = FALSE;
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (int(target_chapters),))
                    return int(cur.fetchone()[0])
        except Exception:
            return 0

    def count_missing_chapters_by_platform(self, target_chapters: int = 10) -> Dict[str, int]:
        """按平台统计仍需补抓章节的书。"""
        sql = """
        WITH books AS (
            SELECT DISTINCT platform, book_id
            FROM crawled_ranking_books
            WHERE COALESCE(book_id, '') <> ''
        ),
        agg AS (
            SELECT platform, book_id,
                   COUNT(*) FILTER (WHERE content <> '') AS content_chapters
            FROM novel_chapters
            GROUP BY platform, book_id
        )
        SELECT b.platform, COUNT(*)
        FROM books b
        LEFT JOIN agg a ON a.platform = b.platform AND a.book_id = b.book_id
        LEFT JOIN novel_content_status s ON s.platform = b.platform AND s.book_id = b.book_id
        WHERE COALESCE(a.content_chapters, 0) < %s
          AND COALESCE(s.exhausted, FALSE) = FALSE
        GROUP BY b.platform ORDER BY b.platform;
        """
        result: Dict[str, int] = {}
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (int(target_chapters),))
                    for platform, count in cur.fetchall():
                        result[platform] = int(count)
        except Exception:
            pass
        return result


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
            days_on_rank=meta["days_on_rank"],
            force_days=True
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
