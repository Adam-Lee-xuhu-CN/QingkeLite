@echo off
echo ==========================================
echo   CLI_lite Desktop - 启动开发模式
echo ==========================================
echo.

REM 检查 PyQt5 是否安装
python -c "import PyQt5" 2>nul
if errorlevel 1 (
    echo [!] PyQt5 未安装，正在安装依赖...
    pip install -r "%~dp0requirements.txt"
)

echo [*] 启动 CLI_lite Desktop...
python "%~dp0main.py"
