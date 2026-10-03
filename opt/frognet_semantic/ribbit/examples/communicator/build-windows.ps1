# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build-windows.ps1 -- the native Windows build of comms-app and comms-feed with Visual Studio (MSVC) and vcpkg.
#   powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
# Finds Visual Studio, enters its x64 developer environment, has vcpkg build the libraries in vcpkg.json (the FIRST run
# compiles Qt and FFmpeg from source: one to two hours, once), builds, and assembles dist\ -- the two programs with
# every DLL and Qt plugin they need, runnable on any Windows machine.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# 1. Visual Studio with the C++ workload
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "Visual Studio not found (no vswhere.exe)" }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio has no C++ compiler: add the 'Desktop development with C++' workload in the Visual Studio Installer" }
Write-Host "Visual Studio: $vs"
Import-Module "$vs\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
foreach ($t in 'cl', 'cmake', 'ninja') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
        throw "$t not found in the Visual Studio environment: add 'C++ CMake tools for Windows' in the Visual Studio Installer"
    }
}

# 2. vcpkg, at EXACTLY this commit -- the pin for every library version: vcpkg uses the recipes in its own folder, so
#    its scripts and its recipes are always the same age. (vcpkg.json carries no "builtin-baseline": that makes vcpkg
#    look the commit up in git history, which a zip download does not have.) Visual Studio's own copy is not used: VS 2022's bundled vcpkg (scripts from Dec 2023) could not run the
#    2026 recipes ("Operation REMOVE_DUPLICATES not recognized", 2026-10-01). Fetched as a zip: no git needed.
$baseline = 'fbb0f7bb200b07a9eb9081c7a3cf51d1aa1c51a1'      # vcpkg master, 2026-10-01: every port and FFmpeg feature checked
$vcpkg = "$PSScriptRoot\vcpkg-$($baseline.Substring(0, 12))"
if (-not (Test-Path "$vcpkg\vcpkg.exe")) {
    if (-not (Test-Path "$vcpkg\bootstrap-vcpkg.bat")) {
        Write-Host "fetching vcpkg $baseline"
        $zip = "$PSScriptRoot\vcpkg-$baseline.zip"
        Invoke-WebRequest -UseBasicParsing "https://github.com/microsoft/vcpkg/archive/$baseline.zip" -OutFile $zip
        Expand-Archive -Force $zip $PSScriptRoot
        Rename-Item "$PSScriptRoot\vcpkg-$baseline" $vcpkg
        Remove-Item $zip
    }
    & "$vcpkg\bootstrap-vcpkg.bat" -disableMetrics
    if (-not (Test-Path "$vcpkg\vcpkg.exe")) { throw "vcpkg did not bootstrap" }
}
$env:VCPKG_ROOT = $vcpkg                                  # not Visual Studio's copy
Write-Host "vcpkg: $vcpkg"

# 3. Configure and build. vcpkg installs vcpkg.json's libraries into build-msvc\vcpkg_installed during configure.
$inst = "$PSScriptRoot\build-msvc\vcpkg_installed\x64-windows"
cmake -G Ninja -B build-msvc -DCMAKE_BUILD_TYPE=Release `
    "-DCMAKE_TOOLCHAIN_FILE=$vcpkg\scripts\buildsystems\vcpkg.cmake" -DVCPKG_TARGET_TRIPLET=x64-windows
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
cmake --build build-msvc --target comms-app comms-feed comms_tuples
if ($LASTEXITCODE -ne 0) { throw "build failed" }

# 4. dist\: the programs, every DLL vcpkg built, and Qt's plugins (the Windows platform plugin above all)
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory dist | Out-Null
Copy-Item build-msvc\comms-app.exe, build-msvc\comms-feed.exe, build-msvc\comms_tuples.dll dist\   # the dll: the Python version's way into the memory
Copy-Item "$inst\bin\*.dll" dist\
$wdq = "$inst\tools\Qt6\bin\windeployqt.exe"
if (Test-Path $wdq) {
    & $wdq --release --no-translations --no-system-d3d-compiler --no-opengl-sw dist\comms-app.exe
} else {
    foreach ($d in 'platforms', 'styles', 'imageformats') {
        if (Test-Path "$inst\Qt6\plugins\$d") { Copy-Item -Recurse "$inst\Qt6\plugins\$d" "dist\$d" }
    }
}
if (-not (Test-Path dist\platforms\qwindows.dll)) { throw "Qt's Windows platform plugin (qwindows.dll) is missing from dist\platforms" }
New-Item -ItemType Directory dist\media | Out-Null
Copy-Item media\stock-720p.mp4 dist\media\
Write-Host ""
Write-Host "built: dist\comms-app.exe dist\comms-feed.exe"
Write-Host "try:   .\dist\comms-app.exe --list-devices"
