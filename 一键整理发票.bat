@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   FapiaoVault - 本地发票整理助手（不联网、不上传）
echo ============================================================
echo.

rem 用法：双击本文件会扫描 samples 示例目录；
rem       把任意发票文件夹拖到本文件图标上，则扫描该文件夹。
set "TARGET=%~dp0samples"
if not "%~1"=="" set "TARGET=%~1"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 Python。请先安装 Python 3.9 或更高版本：
    echo        https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.9 或更高。当前版本：
    python --version
    echo.
    pause
    exit /b 1
)

python -c "import pypdf" >nul 2>nul
if errorlevel 1 (
    echo [提示] 首次运行需要安装 PDF 解析库 pypdf，正在安装（仅此一次）...
    python -m pip install pypdf
    echo.
)

set PYTHONPATH=%~dp0src
echo [信息] 正在扫描：%TARGET%
echo.

python -m fapiao_vault scan "%TARGET%" --archive --open-report
set RC=%errorlevel%

echo.
if "%RC%"=="0" (
    echo 完成。报表在扫描目录下的 "_报表" 文件夹，归档在 "_归档" 文件夹。
) else (
    echo 运行结束，但返回码为 %RC%，请查看上方输出。
)
echo.
pause
