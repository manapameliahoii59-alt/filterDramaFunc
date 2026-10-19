# -*- coding: utf-8 -*-
"""每个常读账号的抓取上限配置（auth_pool/account_limits.json）。

键为账号文件名（如 auth.json / auth_20261018_225027.json），
值为该账号最多可获取的筛选目标剧目数量；0 或缺省表示不限制。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

LIMITS_FILE = Path(__file__).resolve().parent.parent / "auth_pool" / "account_limits.json"
LIMITS_FILENAME = LIMITS_FILE.name
DEFAULT_LIMIT = 300  # 账号未单独配置时的默认上限（0 = 不限制）


def load_limits() -> Dict[str, int]:
    if not LIMITS_FILE.is_file():
        return {}
    try:
        raw = json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: Dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                limit = int(value)
            except (TypeError, ValueError):
                continue
            if limit >= 0:
                result[str(key)] = limit
    return result


def save_limits(limits: Dict[str, int]) -> None:
    LIMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {str(k): int(v) for k, v in limits.items() if int(v) >= 0}
    tmp = LIMITS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LIMITS_FILE)


def get_limit(account_name: str) -> int:
    return int(load_limits().get(account_name, DEFAULT_LIMIT))
