@echo off
title Voice Assistant
cd /d "%~dp0"

rem ===== Check DSH web on 3080 =====
for /f "delims=" %%c in ('curl -s -o nul -w "%%{http_code}" --max-time 3 http://127.0.0.1:3080/') do set CODE=%%c
if not "%CODE%"=="200" (
  echo [voice] DSH web is NOT running on port 3080. Start DeepSeek Harness first.
  pause
  exit /b
)

rem ===== Free port 8899 then start chat history frontend =====
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8899" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1
start /b "" python web_server.py > web_server.log 2>&1

echo.
echo  Voice assistant started. It stays in the SYSTEM TRAY.
echo  Press F2 anywhere to talk, F3 to quit.
echo  Chat history:  http://127.0.0.1:8899
echo.
start "" python voice_gui.py
