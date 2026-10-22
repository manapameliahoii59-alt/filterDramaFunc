# PostgreSQL 数据库变更与字段规范记录 (DATABASE CHANGELOG)

本文档记录 `filterDramaFunc` 项目中涉及远程 PostgreSQL 数据库（特别是 `novel_dna_records` 爆款 DNA 基因库表）的表结构演进、新增字段、索引及业务用途，以便团队后续维护、追溯与环境初始化。

---

## 一、 当前完整表结构定义 (`novel_dna_records`)

**所属表名**：`novel_dna_records`  
**核心作用**：存储网文头部爆款作品经大模型提炼后的 6 大黄金维度 DNA，供短剧第一集原素材多级漏斗筛选、向量检索及加权匹配。  
**唯一键约束**：`book_key` (`{title}_{author}`)

```sql
CREATE TABLE IF NOT EXISTS novel_dna_records (
    -- 基础主键与防重业务键
    id BIGSERIAL PRIMARY KEY,
    book_key VARCHAR(128) NOT NULL UNIQUE,          -- 唯一业务主键: {title}_{author}
    title VARCHAR(128) NOT NULL,                    -- 作品名称
    author VARCHAR(64) NOT NULL,                    -- 作者笔名

    -- 1. 真实榜单排位数据源
    source_platform VARCHAR(32) NOT NULL DEFAULT '番茄小说', -- 起点中文网 / 纵横中文网 / 晋江文学城 / 潇湘书院 / 番茄小说
    rank_name VARCHAR(32) NOT NULL DEFAULT '热读榜',         -- 月票榜 / 新晋作者榜 / 阅读榜 / 畅销榜
    rank_position INT NOT NULL DEFAULT 1,                  -- 排名名次 (1 ~ 100)
    board_type VARCHAR(16) NOT NULL DEFAULT 'NEW_TREND',   -- NEW_TREND (新晋潜力) / MONTHLY_HOT (月度主力) / RETENTION_HOT (高留存追读)

    -- 2. 大盘热度赋分与时效生命周期
    heat_score NUMERIC(5, 2) NOT NULL DEFAULT 85.00,       -- 有效大盘热度分 (20.00 ~ 110.00)
    first_ranked_at DATE DEFAULT CURRENT_DATE,              -- 首次在榜探测日期
    last_ranked_at DATE DEFAULT CURRENT_DATE,               -- 最近一次在榜探测日期
    days_on_rank INT DEFAULT 1,                             -- 累计在榜天数 (衰减核心变量)
    is_currently_on_rank BOOLEAN DEFAULT TRUE,              -- 当前是否在榜活跃
    lifecycle_stage VARCHAR(16) DEFAULT 'SUPER_EXPLODING',  -- SUPER_EXPLODING / PEAK_HARVEST / SATURATED / OVERDUE / DROPPED

    -- 3. 维度1：数据质检与过滤标量
    has_hook_or_leverage_in_opening BOOLEAN DEFAULT TRUE,   -- 【2026-10-22新增】前6章是否已亮出危机与底牌（FALSE为慢热废书自动丢弃）
    extraction_confidence VARCHAR(16) DEFAULT '高',         -- 【2026-10-22新增】大模型提取把握度（高 / 中 / 低）

    -- 4. 维度2：赛道与世界观标量
    gender_track VARCHAR(16) NOT NULL,                      -- 受众赛道（男频 / 女频 / 泛性别）
    worldview_setting VARCHAR(64) DEFAULT '',               -- 【2026-10-22新增】世界观基底（如：现代都市 / 末日天灾 / 规则怪谈 / 玄幻修仙）
    core_sub_genre VARCHAR(64) DEFAULT '',                  -- 【2026-10-22新增】核心细分流派（如：末世囤货安全屋 / 真假千金断亲 / 全家偷听心声）
    main_category VARCHAR(32) NOT NULL,                     -- 【兼容列】主题材（对齐 worldview_setting）
    sub_category VARCHAR(64) NOT NULL,                      -- 【兼容列】细分赛道（对齐 core_sub_genre）
    target_audience VARCHAR(128) DEFAULT '',                -- 【兼容列】开局受迫害弱势状态描述（surface_status）

    -- 5. 维度3：破局底牌标量
    leverage_category VARCHAR(32) DEFAULT '',               -- 【2026-10-22新增】破局底牌6大分类（系统面板类/重生先知类/异能空间类/隐藏马甲类/契约羁绊类/极致智商类）

    -- 6. 维度4：开局冲突与爽点走向标量
    opening_breakthrough_action TEXT DEFAULT '',            -- 【2026-10-22新增】开篇破局动作（前5~6章实质反击或立威）
    subsequent_payoff_trend TEXT DEFAULT '',                -- 【2026-10-22新增】后续打脸闭环走向（重点看第16~50章目录）
    first_counterattack_payoff TEXT DEFAULT '',             -- 【兼容字段】第一集首次反击爽点（对齐 opening_breakthrough_action）

    -- 7. 维度6：两级分层母题数组 (GIN 倒排索引极速比对)
    l1_macro_tropes TEXT[] NOT NULL DEFAULT '{}',           -- L1 宏观母题 (3~5个，带#号)
    l2_micro_elements TEXT[] NOT NULL DEFAULT '{}',         -- L2 微观情节动作与看点 (8~12个，带#号)

    -- 8. 完整 6 维结构化深度基因 JSONB
    dna_profile JSONB NOT NULL,

    -- 9. 状态与审计时间戳
    status VARCHAR(16) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 二、 字段变更历史记录

### 版本 2.1 (2026-10-22 下午)
- **需求背景**：按最新 6 大维度规范对开局与后续爽点进行解耦（破局与闭环分离），章节标题抓取上限扩充至 50 章。
- **物理字段变更**：
  1. `has_hook_or_leverage_in_opening` (`BOOLEAN DEFAULT TRUE`)：
     - **业务用途**：记录前 6 章是否已亮出危机与破局底牌。若大模型提取结果为 `false`，判定为极度慢热废书，代码自动丢弃不入库。
  2. `opening_breakthrough_action` (`TEXT DEFAULT ''`)：
     - **业务用途**：记录开篇破局动作（依据第 1~6 章正文，主角完成的实质性反击或立威），精确对齐短剧第一集原素材剧情骨架。
  3. `subsequent_payoff_trend` (`TEXT DEFAULT ''`)：
     - **业务用途**：记录后续打脸闭环走向（依据第 16~50 章目录），验证小说后续高潮与打脸爽感。
- **采集配置变更**：
  - `novel_trends/config.py` 中的 `DEFAULT_TITLE_COUNT` 从 20 调整为 50（支持新书连载不满 50 章有多少抓多少）。
  - `novel_summary_engine.py` 目录查询提升为 `LIMIT 50`。

### 版本 2.0 (2026-10-22 上午)
- **需求背景**：全面落地 6 大黄金维度结构化特征清单，替代原初版粗粒度字段。
- **物理字段变更**：
  1. `worldview_setting` (`VARCHAR(64) DEFAULT ''`)：
     - **业务用途**：世界观基底，第一层漏斗粗筛时同赛道物理隔离，避免末日与都市跨界混战。
  2. `core_sub_genre` (`VARCHAR(64) DEFAULT ''`)：
     - **业务用途**：核心细分流派（如真假千金断亲、末世囤货安全屋）。
  3. `leverage_category` (`VARCHAR(32) DEFAULT ''`)：
     - **业务用途**：破局底牌 6 大分类。建立 B-Tree 索引 `idx_novel_dna_leverage_cat`，加速脑洞向量比对前的预分类筛选。
  4. `first_counterattack_payoff` (`TEXT DEFAULT ''`)：
     - **业务用途**：第一集首次反击爽点事件。
  5. `extraction_confidence` (`VARCHAR(16) DEFAULT '高'`)：
     - **业务用途**：提取把握度（高/中/低），低把握度废书自动洗掉。
- **索引变更**：
  - 新增 `idx_novel_dna_leverage_cat ON novel_dna_records (leverage_category);`
  - 新增 `idx_novel_dna_core_sub_genre ON novel_dna_records (core_sub_genre);`

---

## 三、 手动补丁迁移脚本 (DDL Patch)

若在全新环境或测试环境中需要手动补丁，可直接在 PostgreSQL 执行以下无损幂等 SQL：

```sql
-- 1. 新增标量列
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS worldview_setting VARCHAR(64) DEFAULT '';
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS core_sub_genre VARCHAR(64) DEFAULT '';
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS leverage_category VARCHAR(32) DEFAULT '';
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS first_counterattack_payoff TEXT DEFAULT '';
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS opening_breakthrough_action TEXT DEFAULT '';
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS subsequent_payoff_trend TEXT DEFAULT '';
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS has_hook_or_leverage_in_opening BOOLEAN DEFAULT TRUE;
ALTER TABLE novel_dna_records ADD COLUMN IF NOT EXISTS extraction_confidence VARCHAR(16) DEFAULT '高';

-- 2. 新增高性能检索索引
CREATE INDEX IF NOT EXISTS idx_novel_dna_leverage_cat ON novel_dna_records (leverage_category);
CREATE INDEX IF NOT EXISTS idx_novel_dna_core_sub_genre ON novel_dna_records (core_sub_genre);

-- 3. 注入中文业务注释
COMMENT ON COLUMN novel_dna_records.worldview_setting IS '【维度2】世界观基底（第一层漏斗粗筛）';
COMMENT ON COLUMN novel_dna_records.core_sub_genre IS '【维度2】核心细分流派';
COMMENT ON COLUMN novel_dna_records.leverage_category IS '【维度3】破局底牌6大分类（脑洞核心检索）';
COMMENT ON COLUMN novel_dna_records.opening_breakthrough_action IS '【维度4】开篇破局动作（前5~6章实质反击或立威，对齐第一集剧本骨架）';
COMMENT ON COLUMN novel_dna_records.subsequent_payoff_trend IS '【维度4】后续打脸闭环走向（重点看第16~50章目录，验证后续高潮爽感）';
COMMENT ON COLUMN novel_dna_records.has_hook_or_leverage_in_opening IS '【维度1】前6章是否已亮出危机与底牌（FALSE为极度慢热废书自动丢弃）';
COMMENT ON COLUMN novel_dna_records.extraction_confidence IS '【维度1】大模型提取把握度（高/中/低）';
```

---

## 四、 代码级 DDL 管理入口

所有表结构与列的自动化维护由以下代码统一调度，客户端启动时会自动探测并平滑补充缺少的列与注释：

- **表结构与索引初始化**：[`novel_trends/pg_storage.py`](novel_trends/pg_storage.py) 中的 `init_database()`
- **注释统一维护**：[`novel_trends/pg_storage.py`](novel_trends/pg_storage.py) 中的 `apply_database_comments()`
- **大模型提取入库映射**：[`novel_trends/novel_summary_engine.py`](novel_trends/novel_summary_engine.py) 中的 `save_dna_to_postgres()`
