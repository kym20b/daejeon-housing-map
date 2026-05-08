@echo off
chcp 65001 >nul
title Install Python Requirements

cd /d "%~dp0"

echo 프로젝트 폴더: %cd%
echo requirements.txt 설치를 시작합니다...
echo.

pip install -r requirements.txt

echo.
echo 설치가 완료되었습니다.
pause
