@echo off
REM Makes Walk-In start automatically when you log in to Windows.
set S=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Walk-In.bat
> "%S%" echo @echo off
>> "%S%" echo start "Walk-In" /min "%~dp0start.bat"
echo Walk-In will now start automatically when you log in.
echo To undo, run autostart_off.bat
pause
