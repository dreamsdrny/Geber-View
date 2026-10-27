@echo off
echo ============================================
echo  Evan_Gerber_View: Building executable...
echo ============================================
echo.

pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

if not exist "Evan_Gerber_View.ico" (
    echo Generating icon...
    python make_icon.py
)

echo Cleaning old build...
if exist "build\Evan_Gerber_View" rmdir /s /q "build\Evan_Gerber_View"
if exist "dist\Evan_Gerber_View"  rmdir /s /q "dist\Evan_Gerber_View"

echo.
echo Building with PyInstaller...
python -m PyInstaller --clean --noconfirm "Evan_Gerber_View.spec"

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo  Build SUCCESS!
    echo  Output: dist\Evan_Gerber_View\Evan_Gerber_View.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED!
    echo ============================================
)
pause