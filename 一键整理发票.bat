@echo off
setlocal
for /f "tokens=2 delims=:" %%C in ('chcp') do set /a OLDCP=%%C
chcp 936 >nul
cd /d "%~dp0"

echo ============================================================
echo   FapiaoVault - 本地发票整理助手（不联网、不上传）
echo ============================================================
echo.

rem 用法：双击本文件会扫描 samples 示例目录；
rem       把任意发票文件夹拖到本文件图标上，则扫描该文件夹。
rem       可选：在本文件同目录放一个"报销配置.txt"，第一行写公司全称，
rem       第二行写 18 位税号，即可自动开启抬头/税号合规校验。
rem       模板见同目录"报销配置.example.txt"。
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

rem ---- 可选：读取报销配置（第一行公司名，第二行税号），自动开启抬头校验 ----
set "CFG=%~dp0报销配置.txt"
set "COMPANY="
set "TAXID="
if exist "%CFG%" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$c=Get-Content -LiteralPath '%CFG%' -Encoding UTF8; if(($c-join '').IndexOf([char]0xFFFD) -ge 0){$c=Get-Content -LiteralPath '%CFG%' -Encoding Default}; $c | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1"`) do set "COMPANY=%%A"
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$c=Get-Content -LiteralPath '%CFG%' -Encoding UTF8; if(($c-join '').IndexOf([char]0xFFFD) -ge 0){$c=Get-Content -LiteralPath '%CFG%' -Encoding Default}; $c | Where-Object { $_.Trim() -ne '' } | Select-Object -First 2 | Select-Object -Last 1"`) do set "TAXID=%%A"
)
if defined COMPANY (
    echo [信息] 已加载 报销配置.txt：公司 "%COMPANY%"，已开启抬头/税号校验
) else (
    echo [信息] 未找到 报销配置.txt，跳过抬头校验（想开启请参考 报销配置.example.txt）
)
echo [信息] 正在扫描：%TARGET%
echo.

set "CMDLINE=scan "%TARGET%" --archive --open-report"
if defined COMPANY set CMDLINE=%CMDLINE% --company "%COMPANY%"
if defined TAXID set CMDLINE=%CMDLINE% --tax-id "%TAXID%"
python -m fapiao_vault %CMDLINE%
set RC=%errorlevel%

echo.
if "%RC%"=="0" (
    echo 完成。报表在扫描目录下的 "_报表" 文件夹，归档在 "_归档" 文件夹。
) else (
    echo 运行结束，但返回码为 %RC%，请查看上方输出。
)
echo.
pause
endlocal & chcp %OLDCP% >nul
