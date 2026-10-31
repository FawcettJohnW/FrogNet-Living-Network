@echo off
rem setup.cmd -- [WINDOWS_SETUP_V1] this computer as an FD&F member that starts with Windows, from the unzipped
rem fdf-windows-<build>.zip. It looks first and says what is missing before it changes anything:
rem   Administrator, 64-bit Windows on x64, wintun.dll beside it, the invitation (it must decode)
rem then keeps the invitation (var\invite) and makes the scheduled task "FrogNet member" (at startup, as SYSTEM) that
rem runs fdf-bridge.cmd, its output in var\fdf-bridge.log, and starts it now.
rem   setup.cmd fdfinv1.<...>     the line fdf-identity NAME --leaf printed on any machine of the region
rem   setup.cmd                   again, with the kept invitation (after an update: unzip over, run this)
rem   setup.cmd remove            the task stops and is removed; var\ (the invitation) is left as it is
setlocal
set "D=%~dp0"
set "TASK=FrogNet member"
net session >nul 2>&1
if errorlevel 1 ( echo setup: run this from a terminal opened as Administrator & exit /b 2 )
if /i "%~1"=="remove" goto remove
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
  echo setup: this package is for 64-bit Windows on x64; this computer is %PROCESSOR_ARCHITECTURE%
  exit /b 2
)
if not exist "%D%wintun.dll" (
  echo setup: wintun.dll is missing: get wintun-0.14.1.zip from https://www.wintun.net and copy bin\amd64\wintun.dll into %D%
  exit /b 2
)
if not exist "%D%var" mkdir "%D%var"
set "FDF_INVITE=%D%var\invite"
if not "%~1"=="" (
  "%D%runtime\bin\java.exe" -cp "%D%lib\*" com.fawcettinnovations.fdf.cli.MainKt keep-invite %1
  if errorlevel 1 ( echo setup: the invitation was refused ^(above^); nothing changed & exit /b 2 )
  rem a new invitation: its region is the forward pond, which the bridge takes when var\forward is not there
  del "%D%var\forward" >nul 2>&1
)
if not exist "%FDF_INVITE%" (
  echo setup: no invitation kept: run  setup.cmd fdfinv1.^<...^>  ^(fdf-identity NAME --leaf, on any machine of the region, prints it^)
  exit /b 2
)
rem the task's command: fdf-bridge.cmd with its output kept, so a start nobody watched can be read afterwards
> "%D%fdf-bridge-task.cmd" (
  echo @echo off
  echo call "%D%fdf-bridge.cmd" ^>^> "%D%var\fdf-bridge.log" 2^>^&1
)
schtasks /end /tn "%TASK%" >nul 2>&1
schtasks /create /tn "%TASK%" /tr "\"%D%fdf-bridge-task.cmd\"" /sc onstart /ru SYSTEM /rl HIGHEST /f >nul
if errorlevel 1 ( echo setup: could not create the scheduled task "%TASK%" & exit /b 1 )
schtasks /run /tn "%TASK%" >nul
echo setup: "%TASK%" made ^(at startup, as SYSTEM^) and started; its output: %D%var\fdf-bridge.log
echo setup: status from any terminal:  "%D%fdf-bridge.cmd" ask status
exit /b 0
:remove
schtasks /end /tn "%TASK%" >nul 2>&1
schtasks /delete /tn "%TASK%" /f >nul 2>&1
echo setup: "%TASK%" stopped and removed; %D%var\ is left as it is
exit /b 0
