"""
main.py
AI 短剧漫剧准入粗筛与商业潜力精筛批处理主程序
工业级双引擎架构：
1. 【两阶段漏斗初筛模式（默认推荐）】：
   - 自动挂载本地多账号调度池 (auth.json / auth_pool)；
   - 本地持久化缓存加速 (14天免重复请求)；
   - 阶段一：常读简介 + 大模型 + 网文库极速快筛（淘汰60%~70%烂剧，截断视频高危请求）；
   - 阶段二：高分晋级剧提取第1集视频，运行本地离线 SenseVoiceSmall 台词识别 + 稠密光流一票否决 PPT 漫；
2. 【离线单机文本模式】：无需常读账号，直接基于本地文本行离线打分。
"""
import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# 兼容 Windows GBK 终端输出，避免 UnicodeEncodeError
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.models import DramaCandidate
from core.cache_manager import ScreeningCacheManager
from core.ai_evaluator import AIDramaEvaluator
from changdu_sync.account_pool import ChangduAccountPool, DEFAULT_AUTH_FILE, DEFAULT_AUTH_POOL_DIR
from core.funnel_screener import FunnelScreeningEngine


def find_target_txt_file(specified_file: Optional[str] = None) -> Optional[str]:
    """智能查找同目录下待处理的 txt 文件"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if specified_file:
        full_path = specified_file if os.path.isabs(specified_file) else os.path.join(base_dir, specified_file)
        if os.path.isfile(full_path):
            return full_path

    preferred_names = ["待筛剧目.txt", "dramas.txt", "input.txt", "剧目列表.txt"]
    for name in preferred_names:
        full_path = os.path.join(base_dir, name)
        if os.path.isfile(full_path):
            return full_path

    exclude_prefixes = ["requirements", "已通过", "已拦截", "passed_", "vetoed_", "report"]
    for f in os.listdir(base_dir):
        if f.lower().endswith(".txt") and not any(f.lower().startswith(ex) for ex in exclude_prefixes):
            return os.path.join(base_dir, f)

    return None


def run_funnel_screening(
    input_file_path: str,
    threshold: float = 55.0,
    skip_cache: bool = False,
    auth_dir: Optional[str] = None,
    no_video: bool = False
):
    """运行两阶段工业级漏斗初筛与精筛"""
    base_dir = Path(__file__).parent.resolve()
    file_name = Path(input_file_path).name

    print("=" * 72)
    print("  🎬 AI 短剧/漫剧工业级准入漏斗筛选系统 (常读物料联动 + 本地ASR + PPT否决)")
    print(f"  待筛文件: {file_name} | 精筛阈值: {threshold}分 | 视频采样: {'禁用' if no_video else '启用 (60秒)'}")
    print("=" * 72)

    # 1. 读取剧目清单
    with open(input_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_lines = f.readlines()

    candidates: List[str] = []
    for line in raw_lines:
        line_clean = line.strip()
        if line_clean and not line_clean.startswith("#") and not line_clean.startswith("//"):
            # 保留完整剧名（含副标题冒号），由检索池自动优先精确匹配与分词
            candidates.append(line_clean)


    total_count = len(candidates)
    if total_count == 0:
        print(f"[错误] 文件 [{file_name}] 中未找到有效剧名！")
        return

    print(f"成功加载待筛剧单: 共 {total_count} 部\n")

    # 2. 检查并初始化常读账号池
    account_pool = None
    has_auth = DEFAULT_AUTH_FILE.is_file() or (DEFAULT_AUTH_POOL_DIR.is_dir() and any(DEFAULT_AUTH_POOL_DIR.glob("*.json")))

    if has_auth:
        try:
            account_pool = ChangduAccountPool(auth_dir=auth_dir, headless=True)
        except Exception as e:
            print(f"[提示] 常读账号池初始化波动 ({e})，降级为纯本地文本模式。")
    else:
        print(f"[提示] 未检测到常读登录态 (auth.json)，走纯本地大模型评估模式。")

    cache_mgr = ScreeningCacheManager()
    ai_eval = AIDramaEvaluator()

    engine = FunnelScreeningEngine(
        account_pool=account_pool,
        cache_manager=cache_mgr,
        ai_evaluator=ai_eval,
        stage2_threshold=threshold,
        skip_cache=skip_cache,
        enable_video=not no_video
    )

    results = []
    s_list = []
    a_list = []
    b_list = []
    vetoed_list = []

    start_t = time.perf_counter()

    try:
        for idx, title in enumerate(candidates, 1):
            cand = DramaCandidate(title=title)
            res = engine.screen_single_drama(cand)
            results.append(res)

            tier = res.get("tier", "C")
            score = res.get("final_score", 0.0)
            entry = f"[{tier}级 | {score:.1f}分] 《{title}》 赛道: 【{res.get('genre_summary', '未知')}】 | 点评: {res.get('ai_review', '')[:60]}..."

            if tier == "S":
                s_list.append(entry)
            elif tier == "A":
                a_list.append(entry)
            elif tier == "B":
                b_list.append(entry)
            else:
                diag = res.get("diagnostic_message", "未达准入线")
                vetoed_list.append(f"[淘汰 | {score:.1f}分] 《{title}》 原因: {diag}")

    finally:
        if account_pool:
            account_pool.close()

    total_cost_sec = time.perf_counter() - start_t

    # 3. 导出筛选结果文件
    passed_file = base_dir / "已通过剧目_passed.txt"
    vetoed_file = base_dir / "已拦截剧目_vetoed.txt"
    report_file = base_dir / "粗筛批处理报告.json"

    passed_blocks = []
    if s_list:
        passed_blocks.append("# ==============================================================================")
        passed_blocks.append(f"# 【S 级 · 爆款潜力推荐】(综合得分 >= 85分 | 共 {len(s_list)} 部)")
        passed_blocks.append("# ==============================================================================")
        passed_blocks.extend(s_list)
        passed_blocks.append("")
    if a_list:
        passed_blocks.append("# ==============================================================================")
        passed_blocks.append(f"# 【A 级 · 保本优质推荐】(综合得分 70-84分 | 共 {len(a_list)} 部)")
        passed_blocks.append("# ==============================================================================")
        passed_blocks.extend(a_list)
        passed_blocks.append("")
    if b_list:
        passed_blocks.append("# ==============================================================================")
        passed_blocks.append(f"# 【B 级 · 平庸待优化】(综合得分 50-69分 | 共 {len(b_list)} 部)")
        passed_blocks.append("# ==============================================================================")
        passed_blocks.extend(b_list)
        passed_blocks.append("")

    with open(passed_file, "w", encoding="utf-8") as f:
        f.write("\n".join(passed_blocks))

    vetoed_blocks = [
        "# ==============================================================================",
        f"# 【负向一票否决淘汰清单】(劣质低分硬伤阻断 | 共 {len(vetoed_list)} 部)",
        "# =============================================================================="
    ] + vetoed_list + [""]

    with open(vetoed_file, "w", encoding="utf-8") as f:
        f.write("\n".join(vetoed_blocks))

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_count": total_count,
                "passed_count": len(s_list) + len(a_list) + len(b_list),
                "vetoed_count": len(vetoed_list),
                "grade_distribution": {
                    "S": len(s_list),
                    "A": len(a_list),
                    "B": len(b_list),
                    "C": len(vetoed_list)
                },
                "total_cost_sec": round(total_cost_sec, 2),
                "avg_cost_sec": round(total_cost_sec / total_count, 2) if total_count else 0
            },
            "results": results
        }, f, ensure_ascii=False, indent=2)

    # 4. 输出最终成果看板
    print("\n" + "=" * 72)
    print("  🏆 AI 短剧漫剧工业级漏斗初筛完成看板")
    print("=" * 72)
    print(f"  - 待筛总数:          {total_count} 部")
    print(f"  - 合格放行数:        {len(s_list) + len(a_list) + len(b_list)} 部 (S级: {len(s_list)}, A级: {len(a_list)}, B级: {len(b_list)})")
    print(f"  - 劣质淘汰数:        {len(vetoed_list)} 部 (含一票否决与低质剧目)")
    print(f"  - 引擎总耗时:        {total_cost_sec:.2f} 秒 (平均单部: {total_cost_sec/total_count:.2f} 秒)")
    print("-" * 72)
    print(f"  [成果导出完成]")
    print(f"  ✓ 优质清单:  {passed_file.name}")
    print(f"  ✓ 淘汰清单:  {vetoed_file.name}")
    print(f"  ✓ 结构化数据: {report_file.name}")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="短剧漫剧工业级漏斗初筛与精筛系统")
    parser.add_argument("--file", "-f", type=str, default=None, help="指定的剧目 txt 文件路径")
    parser.add_argument("--threshold", "-t", type=float, default=55.0, help="阶段一进入阶段二的精筛分水岭分值 (默认 55.0)")
    parser.add_argument("--skip-cache", action="store_true", help="强制穿透缓存重新向常读与AI发起请求")
    parser.add_argument("--auth-dir", type=str, default=None, help="自定义多账号 auth 文件夹路径")
    parser.add_argument("--no-video", action="store_true", help="纯文本模式，跳过第1集音视频切片拉取")
    args = parser.parse_args()

    target_file = find_target_txt_file(args.file)
    if not target_file:
        print("未找到待筛 txt 文件，请检查目录。")
        sys.exit(1)

    run_funnel_screening(
        input_file_path=target_file,
        threshold=args.threshold,
        skip_cache=args.skip_cache,
        auth_dir=args.auth_dir,
        no_video=args.no_video
    )


if __name__ == "__main__":
    main()
