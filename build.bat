@echo off
REM Build MediaGrab into dist\MediaGrab\MediaGrab.exe
REM Pure ASCII + CRLF: cmd parses GBK, Chinese characters in .bat break it.
setlocal
cd /d "%~dp0"

if "%PYTHON%"=="" set PYTHON=python

echo [1/3] Checking Python...
"%PYTHON%" --version || goto :nopython

echo [2/3] Checking PyInstaller...
"%PYTHON%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo       installing PyInstaller...
    "%PYTHON%" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com pyinstaller
)

echo [3/3] Building...
"%PYTHON%" -m PyInstaller build.spec --noconfirm
if errorlevel 1 goto :failed

echo.
echo Done. Output: dist\MediaGrab\MediaGrab.exe
goto :eof

:nopython
echo Python not found. Set PYTHON to a python.exe path and retry.
exit /b 1

:failed
echo Build failed. See messages above.
exit /b 1
