@echo off
chcp 65001 >nul
title Daejeon Housing App - Streamlit

cd /d "%~dp0"

echo [1/2] 프로젝트 폴더: %cd%
echo [2/2] Streamlit 서버를 시작합니다...
echo.
echo 종료하려면 이 창에서 Ctrl + C 를 누르세요.
echo 접속 주소 예시: http://localhost:8501
echo.

streamlit run app.py --server.address 0.0.0.0 --server.port 8501

echo.
echo 서버가 종료되었습니다.
pause
