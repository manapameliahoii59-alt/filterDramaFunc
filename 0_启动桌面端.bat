@echo off
title 短剧粗筛工作台
cd /d "%~dp0"

echo ========================================================
echo   短剧粗筛工作台  (filterDramaFunc 桌面端)
echo   正在检查运行环境...
echo ========================================================
echo.

python -c "import PySide6, qfluentwidgets" 1>nul 2>nul
if errorlevel 1 (
    echo [错误] 当前 python 环境缺少 PySide6 或 qfluentwidgets。
    echo        请先执行: python -m pip install PySide6-Fluent-Widgets
    echo.
    pause
    exit /b 1
)

echo 启动中，请稍候...
python -m desktop_app.app

if errorlevel 1 (
    echo.
    echo ========================================================
    echo   [异常] 桌面端已退出，退出码: %errorlevel%
    echo ========================================================
    pause
)
