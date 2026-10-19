"""
novel_trends/models.py
网络文学头部榜单与短剧风向标数据模型
涵盖起点中文网、纵横中文网、晋江文学城、潇湘书院四大平台
"""
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field


class NovelBook(BaseModel):
    """单部网络文学榜单作品实体"""
    book_id: str = Field(description="平台内唯一书籍ID")
    title: str = Field(description="真实书名")
    author: str = Field(default="未知", description="作者")
    platform: str = Field(description="来源平台：起点中文网 / 纵横中文网 / 晋江文学城 / 潇湘书院")
    channel: str = Field(default="男频", description="频道属性：男频 / 女频")
    rank_name: str = Field(default="月票榜", description="上榜榜单名称：月票榜 / 畅销榜 / 金榜 / 风云榜 等")
    rank_position: int = Field(default=1, description="榜单当前排名位次 (1-20)")
    category: str = Field(default="玄幻", description="题材分类标签 (如: 古代言情, 玄幻仙侠, 豪门世家)")
    intro: str = Field(default="", description="官方简介与核心文案")
    book_url: str = Field(default="", description="书籍详情页或手机端链接")
    
    # 智能增量分析状态
    already_analyzed: bool = Field(default=False, description="是否已经完成过剧情与爆款套路分析 (防重复消耗 Token)")
    analyzed_at: Optional[str] = Field(default=None, description="分析完成时间戳")
    
    # AI 提炼出的剧情与爆款要素
    core_tropes: List[str] = Field(default_factory=list, description="核心爆款套路/母题 (如: 家族修仙, 读心吃瓜, 断亲绝不原谅)")
    hook_keywords: List[str] = Field(default_factory=list, description="吸金关键词与付费卡点标签")
    narrative_arc: str = Field(default="", description="剧情情绪走向与反差弧线")
    heat_score: float = Field(default=80.0, description="根据榜单排位折算的参考热度权重 (80-100分)")


class TrendingTropePattern(BaseModel):
    """提炼出的近期畅销母题模式"""
    pattern_name: str = Field(description="模式名称，如：真千金发疯断亲流")
    channel: str = Field(description="男频 / 女频")
    description: str = Field(description="核心故事公式与爽点来源")
    keywords: List[str] = Field(default_factory=list, description="特征触发关键词")
    representative_books: List[str] = Field(default_factory=list, description="当前榜单上代表作品书名")
    market_bonus_weight: float = Field(default=15.0, description="命中该模式时为短剧提供的加权分 (10-20分)")


class TrendingTropesSummary(BaseModel):
    """大盘题材风向标全貌"""
    channel: str = Field(description="男频 / 女频")
    hot_patterns: List[TrendingTropePattern] = Field(default_factory=list, description="热门母题清单")
    top_keywords: List[str] = Field(default_factory=list, description="高频爆发热词")
    last_updated: str = Field(default="", description="最后汇总时间")


class NovelKnowledgeBase(BaseModel):
    """本地持久化书籍剧情知识库"""
    books: Dict[str, NovelBook] = Field(default_factory=dict, description="已入库书籍字典，Key 为 {platform}_{book_id}")
    male_summary: Optional[TrendingTropesSummary] = Field(default=None, description="男频大盘风向标总结")
    female_summary: Optional[TrendingTropesSummary] = Field(default=None, description="女频大盘风向标总结")
    last_sync_time: Optional[str] = Field(default=None, description="最近一次网络同步时间")


class NovelDnaProfile(BaseModel):
    """6 大黄金维度 + 12~18 个分层热词的标准 DNA 模型"""
    title: str = Field(description="书名")
    author: str = Field(description="作者")
    track_and_audience: Dict[str, Any] = Field(default_factory=dict, description="细分赛道与核心受众画像")
    tone_and_intensity: Dict[str, Any] = Field(default_factory=dict, description="情绪调性与叙事烈度")
    identity_contrast: Dict[str, Any] = Field(default_factory=dict, description="人设与极致身份反差")
    opening_crisis_hook: Dict[str, Any] = Field(default_factory=dict, description="开局第一绝境与冲突钩子")
    payoff_mode: Dict[str, Any] = Field(default_factory=dict, description="破局逆袭与爽点模式")
    hierarchical_tropes: Dict[str, List[str]] = Field(default_factory=dict, description="L1宏观母题(3~5个)与L2微观情节词(8~12个)")
    compliance_risk: Dict[str, str] = Field(default_factory=dict, description="合规与题材风控标记")


class NovelChapter(BaseModel):
    """单章内容（目录标题或正文）"""
    platform: str = Field(description="来源平台")
    book_id: str = Field(description="平台书籍 ID")
    chapter_index: int = Field(default=0, description="章节序号（从 1 开始）")
    chapter_id: str = Field(default="", description="平台章节 ID")
    title: str = Field(default="", description="章节标题")
    content: str = Field(default="", description="正文纯文本（未抓取时为空）")
    is_free: bool = Field(default=True, description="是否免费章节")
    word_count: int = Field(default=0, description="字数")
    url: str = Field(default="", description="章节页 URL")
    crawled_at: str = Field(default="", description="抓取时间")


class NovelIntro(BaseModel):
    """单本书籍的真实简介"""
    platform: str = Field(description="来源平台")
    book_id: str = Field(description="平台书籍 ID")
    title: str = Field(default="", description="书名")
    author: str = Field(default="", description="作者")
    intro: str = Field(default="", description="简介正文")
    url: str = Field(default="", description="书籍详情页 URL")
    crawled_at: str = Field(default="", description="抓取时间")


