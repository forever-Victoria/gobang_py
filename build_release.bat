@echo off
chcp 65001 > nul
cd /d %~dp0
echo ============================================================
echo  打包 Windows 客户端（需已安装 Python 与 pyinstaller）
echo ============================================================

python -c "import PyInstaller" 2>nul || (
    echo [提示] 正在安装 pyinstaller...
    pip install pyinstaller -q
)

echo.
echo [1/2] 正在编译单文件 exe（约 1~2 分钟）...
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
echo [2/2] 复制到 release 目录...
if not exist release mkdir release
copy /y "dist\网络五子棋.exe" release\
copy /y 使用说明.txt release\ 2>nul

echo.
echo ============================================================
echo  完成！只需把这一个文件发给同学：
echo  %CD%\dist\网络五子棋.exe
echo  或：%CD%\release\网络五子棋.exe
echo  对方双击即可，无需 zip、无需 config 文件夹。
echo ============================================================
pause
