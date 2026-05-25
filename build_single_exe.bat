@echo off
chcp 65001 > nul
cd /d %~dp0
echo ============================================================
echo  打包单文件 exe（对方只需双击这一个文件）
echo ============================================================

python -c "import PyInstaller" 2>nul || (
    echo [提示] 正在安装 pyinstaller...
    pip install pyinstaller -q
)

echo.
echo 正在编译（约 1~2 分钟）...
pyinstaller --noconfirm --clean ^
  --windowed ^
  --onefile ^
  --name "网络五子棋" ^
  --paths "%CD%" ^
  --add-data "config\online.json;config" ^
  --hidden-import src.client.settings ^
  --hidden-import src.client.network ^
  --hidden-import src.common.protocol ^
  launch_client.py

if errorlevel 1 (
    echo 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  完成！把下面这个文件发给同学即可（无需 zip、无需 config）：
echo  %CD%\dist\网络五子棋.exe
echo ============================================================
pause
