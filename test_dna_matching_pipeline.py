# -*- coding: utf-8 -*-
"""
test_dna_matching_pipeline.py
端到端验证【方案 A】热门小说 6 维 DNA 特征库与待筛短剧的加权匹配流水线
从待筛剧目中选取代表性短剧，测试其与大盘热门小说特征的匹配打分与诊断归因
"""
import sys
import os
import json

# 修复 Windows 控制台 GBK 编码输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from novel_trends import NovelDnaExtractor, DnaTrendMatcher

# 选取 5 部具有明显对比特征的待筛短剧
SAMPLE_DRAMAS = [
    {
        "title": "真假千金掉包案",
        "synopsis": "十八年前掉包的恩怨，今天在法庭上彻底清算。法官大人，这是当年产房护士的亲笔供词和DNA原始采样记录！真千金绝不原谅偏心父母！",
        "type": "女频真假千金/断亲复仇爆款"
    },
    {
        "title": "豪门千金狂打脸",
        "synopsis": "你以为你伪造了这份亲子鉴定，就能夺走顾家的一切？睁大你的狗眼看清楚，真正执掌顾氏财团百分之五十一股权的大佬是我！假千金与恶毒继母当场下跪！",
        "type": "女频豪门打脸/马甲全能爆款"
    },
    {
        "title": "战神归来：龙帅出狱",
        "synopsis": "三年之期已到，龙帅归位！江城苏家当年陷害我入狱，今天封锁全城机场，通知黑龙军团三千将士，随我奔赴苏家寿宴，百倍奉还！",
        "type": "男频战神回归/入狱复仇爆款"
    },
    {
        "title": "赘婿反击之潜龙在渊",
        "synopsis": "三年入赘受尽江家白眼与羞辱，今天我林辰不再隐忍，林家欠我母亲的公道，今日百倍奉还！",
        "type": "男频赘婿打脸/身份反差"
    },
    {
        "title": "青山绿水纯风景散文",
        "synopsis": "今天天气真好，远处的青山隐没在薄雾中，河水静静流淌，微风吹拂着树叶沙沙作响，阳光温暖地洒向广袤的森林。",
        "type": "负样本（纯风景/无剧情冲突低质剧）"
    }
]


def main():
    print("=" * 72)
    print("  短剧大盘趋势加权流水线：【方案 A】热门小说 6 维 DNA 匹配测试")
    print("=" * 72)

    extractor = NovelDnaExtractor()
    matcher = DnaTrendMatcher(extractor)

    novels = extractor.get_all_profiles()
    print(f"\n[大盘特征库] 当前已加载热门网文底库: {len(novels)} 部")
    for n in novels:
        sub = n.track_and_audience.get('sub_category', '')
        l1 = n.hierarchical_tropes.get('l1_macro_tropes', [])
        print(f"  - 《{n.title}》（{n.author} | {n.track_and_audience.get('gender_track', '')}）: 【{sub}】 | 母题: {l1[:3]}")

    print("\n" + "=" * 72)
    print("  正在执行候选短剧的多维加权比对...")
    print("=" * 72)

    results = []
    lines_md = []
    lines_md.append("# 短剧大盘趋势 DNA 加权匹配流水线测试报告")
    lines_md.append("\n> **匹配引擎**：基于方案 A 提取的 6 维黄金结构与 12~18 个分层热词（L1 宏观 + L2 微观）")
    lines_md.append(f"> **底库规模**：已索引 {len(novels)} 部全网头部热门代表作")
    lines_md.append("\n---\n")

    lines_md.append("## 一、 待筛候选短剧匹配加权得分汇总表\n")
    lines_md.append("| 序号 | 短剧名称 | 短剧类型预期 | 大盘加权分 (0~100) | 命中的最契合网文代表作 | 核心命中母题 | 粗筛建议 |")
    lines_md.append("| :---: | :--- | :--- | :---: | :--- | :--- | :---: |")

    for idx, d in enumerate(SAMPLE_DRAMAS, 1):
        title = d["title"]
        synopsis = d["synopsis"]
        d_type = d["type"]

        res = matcher.match_drama(title, synopsis)
        results.append({
            "drama": d,
            "match_result": res
        })

        score = res["trend_score"]
        novel = res["matched_novel_title"]
        tropes = ", ".join(res["matched_tropes"][:3]) if res["matched_tropes"] else "无"
        
        if score >= 70:
            action = "**强烈推荐优先过审**"
        elif score >= 40:
            action = "进入下一阶段精筛"
        else:
            action = "<span style='color:red;'>大盘不符/低分淘汰</span>"

        lines_md.append(f"| {idx} | 《{title}》 | {d_type} | **{score}** | 《{novel}》 | {tropes} | {action} |")

        print(f"\n[{idx}/5] 待筛剧目: 《{title}》 ({d_type})")
        print(f"  -> 剧目梗概: {synopsis}")
        print(f"  -> 大盘趋势匹配分: {score} 分 (满分 100)")
        print(f"  -> 命中大盘代表作: 《{novel}》（{res['matched_novel_author']}）")
        print(f"  -> 命中的母题标签: {res['matched_tropes']}")
        bd = res.get('breakdown', {})
        print(f"  -> 细分得分: 赛道[{bd.get('track_score',0)}] 调性[{bd.get('tone_score',0)}] 反差[{bd.get('contrast_score',0)}] 冲突[{bd.get('crisis_score',0)}] 热词[{bd.get('tropes_score',0)}]")
        print(f"  -> 算法诊断: {res['evidence_text']}")

    lines_md.append("\n---\n")
    lines_md.append("## 二、 重点案例多维深度诊断\n")

    for idx, item in enumerate(results, 1):
        d = item["drama"]
        r = item["match_result"]
        bd = r.get("breakdown", {})
        lines_md.append(f"### 案例 {idx}：《{d['title']}》 ({d['type']})")
        lines_md.append(f"- **剧目简介**：`{d['synopsis']}`")
        lines_md.append(f"- **大盘加权总分**：**{r['trend_score']} 分**")
        lines_md.append(f"- **分项指标拆解**：")
        lines_md.append(f"  - 细分赛道契合度（满分 20）：`{bd.get('track_score', 0)}` 分")
        lines_md.append(f"  - 情绪调性与烈度（满分 15）：`{bd.get('tone_score', 0)}` 分")
        lines_md.append(f"  - 身份反差模式（满分 20）：`{bd.get('contrast_score', 0)}` 分")
        lines_md.append(f"  - 开局绝境与冲突（满分 20）：`{bd.get('crisis_score', 0)}` 分")
        lines_md.append(f"  - 分层母题热词重合（满分 25）：`{bd.get('tropes_score', 0)}` 分（命中: `{', '.join(r['matched_tropes'])}`）")
        lines_md.append(f"- **智能诊断依据**：{r['evidence_text']}\n")

    # 写出报告文件
    report_path = r"C:\Users\Administrator\Desktop\短剧大盘DNA加权匹配测试报告.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_md))

    print(f"\n全部测试完毕！详细匹配评估报告已生成至: {report_path}")


if __name__ == "__main__":
    main()
