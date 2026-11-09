@echo off
:: The single-file exe extracts its payload into a temp dir (_MEIxxxx) on startup.
:: If the exe carries a Windows "low integrity" label -- this repository folder has
:: one -- Windows refuses to create that temp dir and a box titled "Error" appears:
::     Could not create temporary directory!
:: This script copies the exe outside the repository (%LOCALAPPDATA%) and starts it
:: from there, which avoids the low-integrity label entirely.
:: (The folder build dist\Evan_Gerber_View\Evan_Gerber_View.exe does no runtime
::  extraction, so it is unaffected and can be double-clicked as is.)
:: NOTE: keep this file ASCII-only -- the console code page is not UTF-8, and
::       non-ASCII bytes in a .bat can be mis-parsed by cmd.exe.

setlocal
set "SRC="
if exist "%~dp0Evan_Gerber_View.exe" set "SRC=%~dp0Evan_Gerber_View.exe"
if not defined SRC if exist "%~dp0dist\Evan_Gerber_View.exe" set "SRC=%~dp0dist\Evan_Gerber_View.exe"
if not defined SRC goto :nofile

set "DSTDIR=%LOCALAPPDATA%\Evan_Gerber_View"
set "DST=%DSTDIR%\Evan_Gerber_View.exe"
if not exist "%DSTDIR%" mkdir "%DSTDIR%" >nul 2>&1

copy /y "%SRC%" "%DST%" >nul
if errorlevel 1 goto :copyfail

echo Copied : %SRC%
echo      to: %DST%
echo Launching...
start "" "%DST%" %*
exit /b 0

:nofile
echo [ERROR] Evan_Gerber_View.exe not found.
echo         Looked for: "%~dp0Evan_Gerber_View.exe"
echo                     "%~dp0dist\Evan_Gerber_View.exe"
echo         Build the single-file version first:
echo             python -m PyInstaller --clean --noconfirm Evan_Gerber_View_onefile.spec
echo         Or use the folder version:
echo             "%~dp0dist\Evan_Gerber_View\Evan_Gerber_View.exe"
pause
exit /b 1

:copyfail
echo [ERROR] Failed to copy to "%DST%"
pause
exit /b 1
