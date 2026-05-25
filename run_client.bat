@echo off
chcp 65001 > nul
cd /d %~dp0
start "" pythonw -m src.client.gui --host 127.0.0.1 --port 9527
