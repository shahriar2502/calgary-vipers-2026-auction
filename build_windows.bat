@echo off
setlocal enabledelayedexpansion
rem Calgary Vipers Auction 2026 -- RC2 Windows test-build script.
rem
rem Usage:
rem   build_windows.bat            (normal windowed build -> RC2)
rem   set CVA_DEBUG_CONSOLE=1 & build_windows.bat   (console debug build -> RC2 Debug)
rem
rem Output: dist\Calgary Vipers Auction 2026 RC2\Calgary Vipers Auction 2026.exe
rem     or: dist\Calgary Vipers Auction 2026 RC2 Debug\Calgary Vipers Auction 2026.exe
rem
rem This is a TEST BUILD (RC2), not a final release. See PROJECT_CONTEXT.md's
rem "RC1 Real-Laptop Server Start Hang -- Targeted Debug Pass" section.
rem
rem RC_FOLDER_NAME below MUST match calgary_vipers_auction.spec's
rem DIST_FOLDER_NAME exactly -- update both together when cutting a new RC.
if "%CVA_DEBUG_CONSOLE%"=="1" (
    set RC_FOLDER_NAME=Calgary Vipers Auction 2026 RC2 Debug
) else (
    set RC_FOLDER_NAME=Calgary Vipers Auction 2026 RC2
)

cd /d "%~dp0"

echo === Calgary Vipers Auction 2026 -- RC2 Windows build ===

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv\Scripts\python.exe not found.
    echo Create the virtual environment first:
    echo     python -m venv .venv
    echo     .venv\Scripts\activate
    echo     python -m pip install -r requirements.txt
    echo     python -m pip install -r requirements-build.txt
    exit /b 1
)

set PYTHON=.venv\Scripts\python.exe

"%PYTHON%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo ERROR: PyInstaller is not installed in .venv.
    echo Install build-only dependencies first:
    echo     .venv\Scripts\python.exe -m pip install -r requirements-build.txt
    exit /b 1
)

rem Clean previous build output WITHOUT touching source data, config, or
rem saves -- build\ and dist\ only ever contain generated packaging
rem artifacts, never anything the organizer created (saves\, config\ live
rem at the repository root, entirely outside both of these directories).
rem
rem Only THIS RC's own dist subfolder is removed -- never all of dist\ --
rem so an earlier RC (e.g. RC0) kept there for comparison is never deleted
rem by building a newer one.
if exist "build" (
    echo Removing previous build\ ...
    rmdir /s /q "build"
)
if exist "dist\%RC_FOLDER_NAME%" (
    echo Removing previous "dist\%RC_FOLDER_NAME%" ...
    rmdir /s /q "dist\%RC_FOLDER_NAME%"
)

echo Running PyInstaller (this can take a minute)...
"%PYTHON%" -m PyInstaller calgary_vipers_auction.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 (
    echo.
    echo BUILD FAILED.
    exit /b 1
)

echo.
echo === Build complete ===
echo Output: dist\%RC_FOLDER_NAME%\Calgary Vipers Auction 2026.exe
echo.
echo This is an RC2 TEST BUILD, not a final tournament release.
echo On first launch of Captain Bidding, Windows Firewall may prompt --
echo allow access on PRIVATE networks only.
endlocal
