@echo off
title 批量提取爆款网文DNA特征并入库
cd /d "%~dp0"
echo ========================================================
echo   爆款网文 DNA 特征批量提取引擎 (Qwen3.8-Flash)
echo   默认提取各榜单 TOP 10 头部佳作入库 PostgreSQL
echo ========================================================
echo.
python batch_ingest_dna.py --top-n 10 --workers 3
echo.
echo ========================================================
echo   入库完成！PostgreSQL novel_dna_records 已更新。
echo ========================================================
pause