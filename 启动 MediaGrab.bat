@echo off
setlocal
cd /d "%~dp0"

set "VENV=C:\Users\a1358\.workbuddy\binaries\python\envs\default\Scripts"
set "PYW=%VENV%\pythonw.exe"
set "PYC=%VENV%\python.exe"
set "APP=%~dp0main.py"

if exist "%PYW%" if exist "%APP%" (
    start "" "%PYW%" "%APP%"
    exit /b 0
)

if exist "%PYC%" if exist "%APP%" (
    start "" "%PYC%" "%APP%"
    exit /b 0
)

if exist "%~dp0.venv\Scripts\pythonw.exe" if exist "%APP%" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%APP%"
    exit /b 0
)

echo.
echo  [ERROR] Python environment not found.
echo.
echo  Expected:  %VENV%
echo  Missing:   pythonw.exe / python.exe
echo.
echo  Fix option 1 - restore the bundled venv above.
echo  Fix option 2 - install dependencies and run manually:
echo.
echo      pip install -r requirements.txt
echo      python main.py
echo.
pause
exit /b 1
