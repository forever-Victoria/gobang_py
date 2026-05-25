@echo off
chcp 65001 > nul
cd /d %~dp0
title 网络五子棋 - 连接公网服务器
echo 正在启动客户端，默认连接 %~dp0config\online.json 中的服务器...
start "" pythonw launch_client.py
