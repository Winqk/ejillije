@echo off
setlocal EnableExtensions
title Air Drums
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" if exist ".venv\installed.ok" goto run

echo ============================================================
echo   Air Drums: first launch setup. Needs internet, 2-5 minutes.
echo   Next time the game starts instantly from the desktop icon.
echo ============================================================
echo.

set "PYEXE="
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PYEXE (
    for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
  )
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo Python is not installed. Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE (
  echo.
  echo winget did not work. Downloading Python 3.12 from python.org...
  del "%TEMP%\airdrums-python.exe" >nul 2>&1
  curl.exe -L --fail -o "%TEMP%\airdrums-python.exe" "%PYURL%"
  if not exist "%TEMP%\airdrums-python.exe" powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri $env:PYURL -OutFile $env:TEMP\airdrums-python.exe"
  if exist "%TEMP%\airdrums-python.exe" echo Installing Python, please wait about a minute...
  if exist "%TEMP%\airdrums-python.exe" "%TEMP%\airdrums-python.exe" /quiet InstallAllUsers=0 Include_launcher=0 PrependPath=0 Include_test=0 Include_doc=0
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE goto nopython

echo Python: %PYEXE%
echo.
echo [1/3] Creating a private environment for the game...
"%PYEXE%" -m venv .venv
if errorlevel 1 goto fail
echo [2/3] Downloading components: camera, AI, sound...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
echo ok> ".venv\installed.ok"
echo [3/3] Creating a desktop shortcut...
".venv\Scripts\python.exe" air_drums.py --make-shortcut
echo.
echo Done! Starting the game...

:run
start "" ".venv\Scripts\pythonw.exe" "%~dp0air_drums.py"
exit /b 0

:nopython
echo.
echo Could not find or install Python automatically.
echo Install Python 3.12 from https://www.python.org/downloads/
echo then run this file again.
pause
exit /b 1

:fail
echo.
echo Setup failed, see the messages above.
echo Check the internet connection and run this file again.
pause
exit /b 1
