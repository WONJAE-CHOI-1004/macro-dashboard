@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 거시경제 대시보드를 시작합니다. 잠시 후 브라우저가 열립니다...
py server.py --open
pause
