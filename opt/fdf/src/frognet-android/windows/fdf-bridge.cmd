@echo off
rem fdf-bridge.cmd -- [BRIDGE_WINDOWS_V1] this computer as an FD&F member of every pond in etc\ponds.txt: a leaf of the
rem forward pond (var\forward; the first listed until you change it) and REACHING every other pond its own config allows,
rem all at once, through one Wintun adapter (fdf-tun.exe) -- the same member code the Android app runs.
rem   fdf-bridge.cmd                   run it (as Administrator: Wintun and netsh need it). Ctrl-C or closing the window
rem                                    = leaving every pond; the adapter and its routes go with it.
rem   fdf-bridge.cmd ask resolve w:<guid>:<port>   where that identity is reachable from here (host:port)
rem   fdf-bridge.cmd ask comms [pond]  the Communicator's servers: --ram H:P --media H:P (for comms-app.exe)
rem   fdf-bridge.cmd ask status        the tunnels: handshakes, bytes
rem   fdf-bridge.cmd forward <pond>    bring that pond forward (writes var\forward; the running bridge follows)
setlocal
set "D=%~dp0"
set "FDF_CTL_PORT=7999"
if /i "%~1"=="ask" goto ask
if /i "%~1"=="forward" goto forward
net session >nul 2>&1
if errorlevel 1 (
  echo fdf-bridge: run this from a terminal opened as Administrator ^(Wintun and netsh need it^)
  exit /b 2
)
if not exist "%D%wintun.dll" (
  echo fdf-bridge: wintun.dll is missing: get wintun-0.14.1.zip from https://www.wintun.net and copy bin\amd64\wintun.dll into %D%
  exit /b 2
)
set "FDF_PONDS_FILE=%D%etc\ponds.txt"
set "FDF_FORWARD_FILE=%D%var\forward"
set "FDF_FACTS=%D%var\facts.json"
set "FDF_KEY_FILE=%D%var\wg.key"
set "FDF_LEDGER=%D%var\aliases.json"
set "FDF_TUN_BIN=%D%fdf-tun.exe"
set "FDF_TUN_NAME=FrogNet"
"%D%runtime\bin\java.exe" -cp "%D%lib\*" com.fawcettinnovations.fdf.cli.MainKt bridge
exit /b %errorlevel%
:ask
shift
"%D%runtime\bin\java.exe" -cp "%D%lib\*" com.fawcettinnovations.fdf.cli.MainKt ask %1 %2 %3
exit /b %errorlevel%
:forward
if "%~2"=="" ( echo usage: fdf-bridge.cmd forward ^<pond^> & exit /b 2 )
> "%D%var\forward" echo %~2
echo forward pond: %~2
exit /b 0
