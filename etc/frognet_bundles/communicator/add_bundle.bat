@echo off
REM add_bundle.bat <client_install_dir> <bundle_pkg.tar.gz or folder>
setlocal
set INSTALL=%~1
set PKG=%~2
if "%INSTALL%"=="" echo usage: add_bundle.bat ^<install_dir^> ^<bundle.tar.gz^> & exit /b 2
if not exist "%INSTALL%\bundles" echo not a client install: %INSTALL% & exit /b 1
REM tar is built into Windows 10+; extract and copy the bundle dir(s)
set TMP=%TEMP%\fnbundle_%RANDOM%
mkdir "%TMP%"
tar -xzf "%PKG%" -C "%TMP%"
for /d %%D in ("%TMP%\etc\frognet_bundles\*") do (
  echo installing %%~nxD
  xcopy /e /i /y "%%D" "%INSTALL%\bundles\%%~nxD" >nul
)
rmdir /s /q "%TMP%"
echo done - restart the Communicator to see it.
endlocal
