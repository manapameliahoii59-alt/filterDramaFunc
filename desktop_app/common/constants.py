"""桌面端常量、路径与命令构造。

原则：业务代码零改动。这里只负责「找到脚本」和「拼出与 .bat 一致的命令行」。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "短剧粗筛工作台"
APP_SUBTITLE = "filterDramaFunc 桌面端"
APP_VERSION = "1.0.0"

# desktop_app/common/constants.py -> common -> desktop_app -> 项目根目录
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# 使用当前解释器调用业务脚本，保证依赖与界面完全一致
PYTHON_EXE: str = sys.executable

# ---- 业务脚本入口（与 6 个 .bat 一一对应）----
SCRIPT_MAIN = PROJECT_ROOT / "main.py"
SCRIPT_FETCH = PROJECT_ROOT / "fetch_from_changdu.py"
SCRIPT_TRENDS = PROJECT_ROOT / "sync_novel_trends.py"
SCRIPT_RECORD = PROJECT_ROOT / "record_changdu_requests.py"
SCRIPT_DNA = PROJECT_ROOT / "batch_ingest_dna.py"
SCRIPT_CRAWL_MISSING = PROJECT_ROOT / "crawl_missing_chapters.py"

# ---- 数据文件与产物 ----
AUTH_FILE = PROJECT_ROOT / "auth.json"
AUTH_POOL_DIR = PROJECT_ROOT / "auth_pool"
TODO_TXT = PROJECT_ROOT / "待筛剧目.txt"
PASSED_TXT = PROJECT_ROOT / "已通过剧目_passed.txt"
VETOED_TXT = PROJECT_ROOT / "已拦截剧目_vetoed.txt"
REPORT_JSON = PROJECT_ROOT / "粗筛批处理报告.json"
ENV_FILE = PROJECT_ROOT / ".env"
CACHE_DB = PROJECT_ROOT / "data" / "screening_cache.db"
NOVEL_KB = PROJECT_ROOT / "novel_trends" / "data" / "novel_knowledge_base.json"
REQUEST_DUMP = PROJECT_ROOT / "changdu_network_dump.json"
SCRATCH_DIR = PROJECT_ROOT / "scratch"

# 粗筛可选输入文件（按优先级展示）
PREFERRED_TXT_NAMES = ("待筛剧目.txt", "dramas.txt", "input.txt", "剧目列表.txt")


def build_cmd(script: Path, *args: object) -> list[str]:
    """构造调用业务脚本的命令行，形式与 .bat 内的 `python xxx.py ...` 完全一致。"""
    return [PYTHON_EXE, str(script), *(str(a) for a in args)]


def list_candidate_txt_files() -> list[Path]:
    """列出项目根目录下可作为粗筛输入的 txt 文件。"""
    exclude_prefixes = ("requirements", "已通过", "已拦截", "passed_", "vetoed_", "report")
    found: list[Path] = []
    if not PROJECT_ROOT.is_dir():
        return found
    for f in sorted(PROJECT_ROOT.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".txt":
            continue
        if any(f.name.lower().startswith(ex) for ex in exclude_prefixes):
            continue
        found.append(f)
    found.sort(key=lambda p: (p.name not in PREFERRED_TXT_NAMES, p.name))
    return found


def count_non_empty_lines(path: Path) -> int:
    """统计文本文件的有效行数（忽略空行与 # / // 注释行），与 main.py 口径一致。"""
    if not path.is_file():
        return 0
    total = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                text = line.strip()
                if text and not text.startswith("#") and not text.startswith("//"):
                    total += 1
    except OSError:
        return 0
    return total
