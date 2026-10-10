"""
main.py
短剧漫剧准入粗筛（负向一票否决）主程序
功能：
1. 自动读取同目录下的 .txt 文件（每行代表一部待筛剧目）
2. 逐行执行快速负向一票否决检测
3. 实时输出检测进度，生成已通过/已拦截清单与结构化报告
"""
import os
import sys
import json
import time
import argparse
from typing import List, Dict, Tuple, Optional

# 兼容 Windows GBK 终端输出，避免 UnicodeEncodeError
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core import CoarseFilterPipeline, CoarseFilterConfig, CoarseFilterResult


def find_target_txt_file(specified_file: Optional[str] = None) -> Optional[str]:
    """智能查找同目录下待处理的 txt 文件"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. 若命令行显式指定
    if specified_file:
        full_path = specified_file if os.path.isabs(specified_file) else os.path.join(base_dir, specified_file)
        if os.path.isfile(full_path):
            return full_path

    # 2. 优先匹配常用命名
    preferred_names = ["待筛剧目.txt", "dramas.txt", "input.txt", "剧目列表.txt"]
    for name in preferred_names:
        full_path = os.path.join(base_dir, name)
        if os.path.isfile(full_path):
            return full_path

    # 3. 自动扫描同目录下其它 txt 文件（排除系统/依赖/输出文件）
    exclude_prefixes = ["requirements", "已通过", "已拦截", "passed_", "vetoed_", "report"]
    candidates = []
    for f in os.listdir(base_dir):
        if f.lower().endswith(".txt"):
            if not any(f.lower().startswith(ex) for ex in exclude_prefixes):
                candidates.append(os.path.join(base_dir, f))

    if candidates:
        return candidates[0]

    return None


def parse_drama_line(line_str: str, index: int) -> Tuple[str, str, bool]:
    """
    解析单行剧目内容
    返回: (剧目名称, 待检文本或视频路径, 是否为视频文件)
    """
    line_clean = line_str.strip()
    
    # 检查是否为本地视频文件路径
    if os.path.isfile(line_clean) and any(line_clean.lower().endswith(ext) for ext in [".mp4", ".mov", ".mkv", ".avi"]):
        drama_title = os.path.basename(line_clean)
        return drama_title, line_clean, True

    # 文本剧本处理：尝试提取标题前缀（如《剧名》或 剧名：）
    drama_title = f"剧目_{index:03d}"
    content = line_clean

    # 1. 优先匹配 《完整剧名》 模式（避免剧名内部带副标题冒号被错误切断）
    if line_clean.startswith("《") and "》" in line_clean:
        end_idx = line_clean.find("》")
        drama_title = line_clean[:end_idx + 1].strip()
        content = line_clean[end_idx + 1:].lstrip("：: \t").strip()
    # 2. 匹配 剧名：内容 模式
    elif "：" in line_clean:
        parts = line_clean.split("：", 1)
        if len(parts[0]) <= 35 and len(parts[1]) > 0:
            drama_title = parts[0].strip()
            content = parts[1].strip()
    elif ":" in line_clean:
        parts = line_clean.split(":", 1)
        if len(parts[0]) <= 35 and len(parts[1]) > 0:
            drama_title = parts[0].strip()
            content = parts[1].strip()

    return drama_title, content, False


REASON_CODE_NAMES = {
    "DETECTED_FEW_EPISODES": "总集数不足35集",
    "DETECTED_SHORT_EPISODE_1": "第一集时长不足50秒",
    "DETECTED_PPT_SLIDESHOW": "PPT漫伪动态漫",
    "DETECTED_ROUGH_DOODLE_OR_MEME": "沙雕表情包涂鸦漫",
    "DETECTED_LOW_INFORMATION_DENSITY": "严重注水车轱辘吵架",
    "DETECTED_PROMPT_POLLUTION": "残留分镜提示词标签",
    "DETECTED_NO_CLEAR_PROTAGONIST": "缺失核心主角主体",
    "DETECTED_LACK_DRAMATIC_CONFLICT": "缺乏戏剧对抗冲突",
    "DETECTED_SEVERE_BLUR": "画面严重失焦模糊",
    "DETECTED_SEVERE_BLACK_SCREEN": "大面积黑屏断流",
    "DETECTED_FROZEN_VIDEO": "画面严重冻结卡顿",
    "DETECTED_AUDIO_SILENCE": "音频大段持续静音",
    "DETECTED_AUDIO_CLIPPING": "音频严重破音爆音",
}


def run_batch_filter(input_file_path: str, fast_fail: bool = True):
    """批量执行两阶段准入筛选流（粗筛负向一票否决 + 精筛商业潜力分级）"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_name = os.path.basename(input_file_path)

    print("=" * 68)
    print("  AI 短剧/漫剧准入粗筛（负向一票否决淘汰劣质剧）引擎")
    print(f"  读取文件: {file_name}")
    print("=" * 68)

    # 读取全部非空行
    with open(input_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_lines = f.readlines()

    drama_items = []
    for idx, raw_line in enumerate(raw_lines, 1):
        cleaned = raw_line.strip()
        # 跳过空行和注释行
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("//"):
            continue
        title, content, is_video = parse_drama_line(cleaned, idx)
        drama_items.append((title, content, is_video, cleaned))

    total_count = len(drama_items)
    if total_count == 0:
        print(f"警告：文件 [{file_name}] 中未找到有效待筛剧目内容！")
        return

    print(f"成功加载待筛剧目: {total_count} 部\n")

    config = CoarseFilterConfig(fast_fail=fast_fail)
    pipeline = CoarseFilterPipeline(config=config)

    s_list = []
    a_list = []
    b_list = []
    vetoed_list = []
    reports = []
    reason_counter = {}

    start_total_t = time.perf_counter()

    for idx, (title, content, is_video, original_line) in enumerate(drama_items, 1):
        if is_video:
            res: CoarseFilterResult = pipeline.filter_drama(video_path=content, drama_title=title)
        else:
            text_to_filter = original_line if original_line else content
            res: CoarseFilterResult = pipeline.filter_drama(dialogue_text=text_to_filter, drama_title=title)

        cost_str = f"{res.total_execution_time_ms:.2f}ms"
        
        # 格式化终端输出
        if res.passed:
            status_tag = f"[PASS 放行 | {res.grade} 级 ({res.composite_score:.1f}分)]"
            print(f"[{idx:02d}/{total_count:02d}] {status_tag} {title} (耗时: {cost_str})")
            
            # 显示多维打分与网文大盘加分
            dims = res.dimension_scores
            dim_str = (
                f"画面:{dims.get('visual_form',0):.0f} | 规格:{dims.get('spec_scale',0):.0f} | "
                f"密度:{dims.get('dialogue_density',0):.0f} | 冲突:{dims.get('character_conflict',0):.0f}"
            )
            print(f"      ├─ 基础维度分: {dim_str}")
            
            trend_bonus = dims.get('trend_bonus', 0.0)
            if trend_bonus > 0 and res.trend_evidence:
                ev_text = res.trend_evidence.get('evidence_text', '')
                print(f"      └─ 榜单风向加权: +{trend_bonus:.1f}分 ({ev_text})")
        else:
            status_tag = f"[VETO 淘汰 | {res.composite_score:.1f}分]"
            friendly_reasons = [REASON_CODE_NAMES.get(r, r) for r in res.veto_reasons]
            reasons_tag = f"【{','.join(friendly_reasons)}】" if friendly_reasons else "【综合得分未达60分准入线】"
            print(f"[{idx:02d}/{total_count:02d}] {status_tag} {title} {reasons_tag} (耗时: {cost_str})")
            
            dims = res.dimension_scores
            dim_str = (
                f"画面:{dims.get('visual_form',0):.0f} | 规格:{dims.get('spec_scale',0):.0f} | "
                f"密度:{dims.get('dialogue_density',0):.0f} | 冲突:{dims.get('character_conflict',0):.0f}"
            )
            print(f"      ├─ 基础维度分: {dim_str}")
            for d in res.diagnostics:
                print(f"      └─ 核心失分项: {d}")
            for fr in friendly_reasons:
                reason_counter[fr] = reason_counter.get(fr, 0) + 1

        item_report = {
            "index": idx,
            "title": title,
            "passed": res.passed,
            "action": res.action,
            "grade": res.grade,
            "composite_score": res.composite_score,
            "dimension_scores": res.dimension_scores,
            "trend_evidence": res.trend_evidence,
            "risk_level": res.risk_level,
            "veto_reasons": res.veto_reasons,
            "diagnostics": res.diagnostics,
            "cost_ms": res.total_execution_time_ms,
            "original_content": original_line
        }
        reports.append(item_report)

        if res.passed:
            entry_line = f"[{res.grade}级 | {res.composite_score:.1f}分] {original_line}"
            if res.grade == "S":
                s_list.append(entry_line)
            elif res.grade == "A":
                a_list.append(entry_line)
            else:
                b_list.append(entry_line)
        else:
            friendly_reasons = [REASON_CODE_NAMES.get(r, r) for r in res.veto_reasons]
            reasons_str = ",".join(friendly_reasons) if friendly_reasons else "得分不足60分"
            diag_str = " | ".join(res.diagnostics)
            vetoed_list.append(f"[淘汰 | {res.composite_score:.1f}分 | {reasons_str}] {title} 原因: {diag_str} 原文: {content}")

    total_elapsed_ms = (time.perf_counter() - start_total_t) * 1000.0
    avg_elapsed_ms = total_elapsed_ms / total_count if total_count > 0 else 0.0

    # 导出文件
    passed_file_path = os.path.join(base_dir, "已通过剧目_passed.txt")
    vetoed_file_path = os.path.join(base_dir, "已拦截剧目_vetoed.txt")
    report_file_path = os.path.join(base_dir, "粗筛批处理报告.json")

    # 构建分组结构清晰的已通过剧目列表
    passed_content_blocks = []
    if s_list:
        passed_content_blocks.append("# ==============================================================================")
        passed_content_blocks.append(f"# 【S 级 · 爆款潜力推荐】(综合得分 >= 80分 | 强情绪张力 + 强付费卡点 + 微创新 | 共 {len(s_list)} 部)")
        passed_content_blocks.append("# ==============================================================================")
        passed_content_blocks.extend(s_list)
        passed_content_blocks.append("")

    if a_list:
        passed_content_blocks.append("# ==============================================================================")
        passed_content_blocks.append(f"# 【A 级 · 保本优质推荐】(综合得分 65-79分 | 经典爆款骨架 + 节奏紧凑 | 共 {len(a_list)} 部)")
        passed_content_blocks.append("# ==============================================================================")
        passed_content_blocks.extend(a_list)
        passed_content_blocks.append("")

    if b_list:
        passed_content_blocks.append("# ==============================================================================")
        passed_content_blocks.append(f"# 【B 级 · 平庸待优化】(综合得分 45-64分 | 叙事平缓偏淡，建议脚本微调 | 共 {len(b_list)} 部)")
        passed_content_blocks.append("# ==============================================================================")
        passed_content_blocks.extend(b_list)
        passed_content_blocks.append("")

    with open(passed_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(passed_content_blocks))

    # 构建已拦截清单
    vetoed_blocks = [
        "# ==============================================================================",
        f"# 【负向一票否决淘汰清单】(劣质低分硬伤阻断，严禁投入后续生产 | 共 {len(vetoed_list)} 部)",
        "# =============================================================================="
    ] + vetoed_list + [""]

    with open(vetoed_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(vetoed_blocks))

    # 构建结构化 JSON 评估报告
    total_passed = len(s_list) + len(a_list) + len(b_list)
    total_vetoed = len(vetoed_list)
    veto_rate = (total_vetoed / total_count) if total_count > 0 else 0.0

    with open(report_file_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_count": total_count,
                "passed_count": total_passed,
                "vetoed_count": total_vetoed,
                "interception_rate": f"{veto_rate:.1%}",
                "grade_distribution": {
                    "S": len(s_list),
                    "A": len(a_list),
                    "B": len(b_list),
                    "C": total_vetoed
                },
                "veto_reasons_breakdown": reason_counter,
                "total_cost_ms": round(total_elapsed_ms, 2),
                "avg_cost_ms": round(avg_elapsed_ms, 2)
            },
            "results": reports
        }, f, ensure_ascii=False, indent=2)

    # 打印最终统计看板
    print("\n" + "=" * 68)
    print("  低分劣质短剧粗筛淘汰完成看板 (负向一票否决)")
    print("=" * 68)
    print(f"  - 待筛总数:          {total_count} 部")
    print(f"  - 合格放行数:        {total_passed} 部 ({(total_passed / total_count * 100):.1f}%)")
    print(f"  - 劣质淘汰数:        {total_vetoed} 部 ({(total_vetoed / total_count * 100):.1f}%)")
    if reason_counter:
        print("  - 劣质败因分布:")
        for r_name, r_cnt in reason_counter.items():
            print(f"      × {r_name}: {r_cnt} 部")
    print(f"  - 平均单部耗时:      {avg_elapsed_ms:.2f} 毫秒")
    print(f"  - 引擎总耗时:        {total_elapsed_ms:.2f} 毫秒")
    print("-" * 68)
    print(f"  [文件导出成功]")
    print(f"  -> 合格清单:  已通过剧目_passed.txt (S级 {len(s_list)}部, A级 {len(a_list)}部, B级 {len(b_list)}部)")
    print(f"  -> 淘汰清单:  已拦截剧目_vetoed.txt (共淘汰 {total_vetoed}部，附带明确败因)")
    print(f"  -> 结构化报告: 粗筛批处理报告.json")
    print("=" * 68)


def main():
    parser = argparse.ArgumentParser(description="短剧漫剧准入粗筛批处理主程序")
    parser.add_argument("--file", "-f", type=str, default=None, help="指定的剧目 txt 文件路径")
    parser.add_argument("--full-audit", action="store_true", help="关闭短路模式，执行全算子审计")
    args = parser.parse_args()

    # 自动定位 txt 文件
    target_file = find_target_txt_file(args.file)

    if not target_file:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        print(f"未找到可处理的剧目 txt 文件！")
        print(f"请在当前目录 [{base_dir}] 下放入待筛文本文件（如 待筛剧目.txt），每行代表一部剧。")
        sys.exit(1)

    run_batch_filter(target_file, fast_fail=not args.full_audit)


if __name__ == "__main__":
    main()
