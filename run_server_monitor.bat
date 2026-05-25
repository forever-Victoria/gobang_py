@echo off
chcp 65001 > nul
cd /d %~dp0
echo ============================================================
echo  GoBang Server Monitor - listening on 0.0.0.0:9527
echo  Close the monitor window to stop the server.
echo ============================================================
python -m src.server.monitor --host 0.0.0.0 --port 9527
pause
