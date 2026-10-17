@echo off
title Walk-In
cd /d "%~dp0"
echo.
echo  Starting Walk-In...
echo.

REM 1. Keep the laptop awake while plugged in (so the booking page stays online)
powercfg /change standby-timeout-ac 0 >nul 2>&1

REM 2. AI: start Ollama if it is not already running
tasklist /fi "imagename eq ollama.exe" | find /i "ollama.exe" >nul
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
  ) else (
    start "" /min ollama serve
  )
  echo  [ok] AI started
) else (
  echo  [ok] AI already running
)

REM 3. Permanent public link: ngrok if ngrok_domain.txt exists, otherwise Tailscale Funnel
if exist ngrok_domain.txt (
  set /p NGDOM=<ngrok_domain.txt
  goto :ngrok
)
set TS=tailscale
where tailscale >nul 2>&1 || set TS="C:\Program Files\Tailscale\tailscale.exe"
%TS% status 2>nul | findstr /i "offline" >nul
if not errorlevel 1 (
  echo  [!!] Tailscale is OFFLINE on this network, so phones cannot open your link.
  echo       Switch this laptop to another Wi-Fi or a phone hotspot, or set up ngrok.
)
REM Always re-register the public link: Funnel can say "on" while its public address has dropped.
echo  Turning on the public link. If a login.tailscale.com link appears below,
echo  open it in your browser and click Enable, then come back here.
%TS% funnel reset >nul 2>&1
%TS% funnel --bg 8000
%TS% funnel status 2>nul | findstr /i "https://"
goto :linkdone

:ngrok
for /f "tokens=* delims= " %%A in ("%NGDOM%") do set NGDOM=%%A
where ngrok >nul 2>&1
if errorlevel 1 (
  echo  [!!] ngrok is not installed. Run: winget install ngrok.ngrok
) else (
  tasklist /fi "imagename eq ngrok.exe" | find /i "ngrok.exe" >nul
  if errorlevel 1 start "ngrok" /min ngrok http --url=%NGDOM% 8000
  echo  [ok] Public link: https://%NGDOM%
)

:linkdone
echo.

REM 4. Open the app in the browser a few seconds after the server starts
start "" cmd /c "timeout /t 6 >nul & start http://localhost:8000/app"

echo.
echo  [ok] Walk-In is running. Keep this window open. Close it to stop.
echo       Backups are saved automatically once a day in the "backups" folder.
echo.
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
echo.
echo  Walk-In stopped.
pause
