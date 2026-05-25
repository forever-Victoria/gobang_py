@echo off
chcp 65001 > nul
cd /d %~dp0
echo ============================================================
echo  GoBang Server - listening on 0.0.0.0:9527
echo  Logs: %~dp0logs\server.log
echo  Press Ctrl+C to stop.
echo ============================================================
python -m src.server.server --host 0.0.0.0 --port 9527
pause
