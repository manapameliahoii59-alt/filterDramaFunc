@echo off
title 批量提取小说核心剧情DNA入库
cd /d "%~dp0"
echo ========================================================
echo   小说核心剧情 DNA 批量提取 (Qwen3.8-Flash)
echo   默认提取各榜单 TOP 10 头部作品并入库至 PostgreSQL
echo ========================================================
echo.
python batch_ingest_dna.py --top-n 10 --workers 3
if errorlevel 1 (
    echo.
    echo ========================================================
    echo   [错误] DNA提取异常中断，退出码: %errorlevel%
    echo ========================================================
) else (
    echo.
    echo ========================================================
    echo   提取完成！PostgreSQL novel_dna_records 数据库表已更新。
    echo ========================================================
)
echo.
pause
