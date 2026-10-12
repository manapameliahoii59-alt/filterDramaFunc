"""
models.py
定义粗筛数据模型与配置协议
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class DetectorResult(BaseModel):
    """单个检测算子的运行结果"""
    detector_name: str
    triggered_veto: bool = Field(description="是否触发了一票否决")
    reason_code: Optional[str] = Field(default=None, description="违规拦截错误码")
    diagnostic_message: Optional[str] = Field(default=None, description="排查诊断评语")
    metric_scores: Dict[str, float] = Field(default_factory=dict, description="量化指标明细")
    cost_time_ms: float = Field(default=0.0, description="算子耗时（毫秒）")


class CoarseFilterConfig(BaseModel):
    """粗筛流水线全局配置"""
    # 调度策略
    fast_fail: bool = Field(default=True, description="是否开启短路模式（遇到致命负向立刻返回）")
    
    # 1. PPT漫检测阈值
    ppt_rigid_ratio_threshold: float = Field(default=0.85, description="刚体光流比例阈值")
    ppt_min_frames_to_sample: int = Field(default=15, description="抽样检测帧数")
    
    # 2. 沙雕简笔画检测阈值
    doodle_edge_density_max: float = Field(default=0.018, description="过低边缘密度上限（低于此值疑为极简涂鸦）")
    doodle_color_entropy_max: float = Field(default=1.80, description="过低色彩直方图熵上限")
    
    # 3. 画质硬伤阈值
    blur_laplacian_min: float = Field(default=60.0, description="最小清晰度拉普拉斯方差")
    black_frame_ratio_max: float = Field(default=0.35, description="最大黑屏/冻结帧比例")
    
    # 4. 叙事信息密度阈值
    redundancy_ratio_max: float = Field(default=0.50, description="对白重复冗余度上限")
    min_ttr_ratio: float = Field(default=0.35, description="词汇多样性TTR下限")
    
    # 5. 音频故障阈值
    silence_ratio_max: float = Field(default=0.30, description="静音时长最大占比")
    clipping_ratio_max: float = Field(default=0.05, description="破音削波样本最大比例")

    # 6. 规格体量与集数时长门槛
    min_total_episodes: int = Field(default=35, description="总集数门槛（必须 > 35 集）")
    min_ep1_duration_sec: float = Field(default=50.0, description="第一集时长门槛（必须 > 50 秒）")



class FineScreeningScore(BaseModel):
    """精筛商业潜力评分结果"""
    story_tension: float = Field(ge=0.0, le=40.0, description="故事情节张力分 (0-40)")
    narrative_pacing: float = Field(ge=0.0, le=30.0, description="叙事节奏密度分 (0-30)")
    novelty_bonus: float = Field(ge=0.0, le=30.0, description="时效与微创新分 (0-30)")
    total_score: float = Field(ge=0.0, le=100.0, description="综合商业潜力总分 (0-100)")
    grade: str = Field(description="评级: S (爆款潜力) / A (保本优质) / B (平庸待优化) / C (淘汰)")
    highlights: List[str] = Field(default_factory=list, description="剧情爽点与亮点标签")


class CoarseFilterResult(BaseModel):
    """筛选全流程最终输出 (多维综合加权打分 + 网文榜单题材加成)"""
    passed: bool = Field(description="是否通过粗筛进入精筛")
    action: str = Field(description="决策动作: PASS (通过) / VETO (低分未达标淘汰)")
    grade: str = Field(default="C", description="最终综合评级: S (>=80分) / A (65-79分) / B (60-64分) / C (淘汰)")
    composite_score: float = Field(default=0.0, description="多维加权综合总得分 (0-100分)")
    dimension_scores: Dict[str, float] = Field(default_factory=dict, description="各维度得分明细 (画面/规格/密度/对抗/视听)")
    trend_evidence: Optional[Dict[str, Any]] = Field(default=None, description="真实网文榜单风向标与爆款书籍同款匹配依据")
    risk_level: Optional[str] = Field(default=None, description="风险等级 (已废弃)")
    veto_reasons: List[str] = Field(default_factory=list, description="命中严重失分或淘汰原因列表")
    diagnostics: List[str] = Field(default_factory=list, description="归因排查详情")
    scores: Dict[str, float] = Field(default_factory=dict, description="汇总特征指标字典")
    fine_screening: Optional[FineScreeningScore] = Field(default=None, description="精筛打分与分级详情")
    detector_details: List[DetectorResult] = Field(default_factory=list, description="各算子明细")
    total_execution_time_ms: float = Field(default=0.0, description="总耗时（毫秒）")
