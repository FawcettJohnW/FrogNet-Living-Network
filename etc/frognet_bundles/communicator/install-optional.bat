@echo off
REM FrogNet Communicator - Windows installer (from the unpacked zip payload).
REM Installs the single-directory client to %LOCALAPPDATA%\FrogNetCommunicator and makes
REM Start Menu + Desktop shortcuts. No admin needed. Re-runnable (keeps bundles you added).
REM Requires Python 3 on PATH (with tkinter, which is included in the python.org installer).
setlocal enabledelayedexpansion
set SRC=%~dp0
set APP=%LOCALAPPDATA%\FrogNetCommunicator

where python >nul 2>nul || (echo Python 3 is required on PATH ^(python.org installer^). & pause & exit /b 1)

echo Installing to %APP%
if not exist "%APP%" mkdir "%APP%"
copy /y "%SRC%*.py" "%APP%\" >nul
copy /y "%SRC%run_communicator.bat" "%APP%\" >nul
copy /y "%SRC%run_communicator.sh" "%APP%\" >nul 2>nul
if not exist "%APP%\bundles" mkdir "%APP%\bundles"
REM merge ship-with bundles without clobbering user-added ones
for /d %%D in ("%SRC%bundles\*") do (
  if not exist "%APP%\bundles\%%~nxD" xcopy /e /i /y "%%D" "%APP%\bundles\%%~nxD" >nul
)

REM Start Menu + Desktop shortcuts via PowerShell (no extra tooling)
set SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$s=$w.CreateShortcut('%SM%\FrogNet Communicator.lnk');" ^
  "$s.TargetPath='%APP%\run_communicator.bat';" ^
  "$s.WorkingDirectory='%APP%';" ^
  "$s.IconLocation='%SystemRoot%\System32\shell32.dll,13';" ^
  "$s.Save();" ^
  "$d=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\FrogNet Communicator.lnk');" ^
  "$d.TargetPath='%APP%\run_communicator.bat';" ^
  "$d.WorkingDirectory='%APP%';" ^
  "$d.Save()"

echo.
echo Installed. Launch from the Start Menu / Desktop "FrogNet Communicator",
echo or run: "%APP%\run_communicator.bat" --host ^<FrogNetHost^> --name ^<you^>
echo App dir: %APP%
echo Add a game later: add_bundle.bat "%APP%" ^<bundle.tar.gz^>
pause
endlocal
