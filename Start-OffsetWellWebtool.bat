@echo off
rem ==========================================================================
rem  Offset Well Webtool - start on a Windows laptop
rem
rem  Double-click this file. The first start takes 3 to 10 minutes.
rem  Later starts take a few seconds.
rem
rem  What it does:
rem   1. Finds Python 3.11 or later. If there is none, it downloads Python
rem      from python.org and installs it for your user only (no admin rights).
rem   2. Makes a private Python environment for the app and installs the
rem      packages from pypi.org.
rem   3. Downloads the NOAA NADCON grids for exact NAD27 to NAD83 shifts.
rem   4. Starts the app at http://127.0.0.1:8000 and opens your browser.
rem      Only this laptop can open the app.
rem
rem  Close this window to stop the app.
rem
rem  Everything goes in %LOCALAPPDATA%\OffsetWellWebtool. Delete that folder
rem  to remove the app. Your jobs and downloaded files are in its "data" folder.
rem
rem  Optional settings (set them before you start this file):
rem   OWT_PORT             port for the app (default 8000)
rem   OWT_HOME             folder for Python, packages and data
rem   OWT_PYTHON_VERSION   Python version to download if none is found
rem   HTTPS_PROXY          proxy for downloads, for example http://proxy:8080
rem   ANTHROPIC_API_KEY    Claude API key. Or put the key in the file
rem                        %LOCALAPPDATA%\OffsetWellWebtool\anthropic_key.txt
rem ==========================================================================
setlocal EnableExtensions
title Offset Well Webtool
cd /d "%~dp0"

set "APP_DIR=%~dp0"
if "%OWT_HOME%"=="" set "OWT_HOME=%LOCALAPPDATA%\OffsetWellWebtool"
if "%OWT_PORT%"=="" set "OWT_PORT=8000"
if "%OWT_PYTHON_VERSION%"=="" set "OWT_PYTHON_VERSION=3.13.1"
set "VENV=%OWT_HOME%\venv"
set "VPY=%OWT_HOME%\venv\Scripts\python.exe"
set "PY_HOME=%OWT_HOME%\python"
set "REQ=%APP_DIR%backend\requirements.txt"
set "URL=http://127.0.0.1:%OWT_PORT%"

echo.
echo  Offset Well Webtool
echo  -------------------
echo.

if not exist "%REQ%" goto :err_no_app
if not exist "%OWT_HOME%" mkdir "%OWT_HOME%"

rem ---- the app is already running in another window -----------------------
call :is_running
if not errorlevel 1 goto :already_running

rem ---- proxy: use the Windows proxy setting if HTTPS_PROXY is not set ------
if not "%HTTPS_PROXY%"=="" goto :proxy_done
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try { $p = [Net.WebRequest]::GetSystemWebProxy().GetProxy('https://pypi.org/simple/'); if ($p.Host -ne 'pypi.org') { $p.AbsoluteUri } } catch {}" 2^>nul`) do set "HTTPS_PROXY=%%P"
if not "%HTTPS_PROXY%"=="" echo  Proxy from Windows settings: %HTTPS_PROXY%
:proxy_done

rem ---- Python environment for the app -------------------------------------
if not exist "%VPY%" goto :make_venv
fc /b "%REQ%" "%VENV%\owt-requirements.txt" >nul 2>&1
if errorlevel 1 goto :install_packages
goto :grids

:make_venv
call :find_python
if not defined PY call :install_python
if not defined PY goto :err_no_python
echo  Making the Python environment for the app...
%PY% -m venv "%VENV%"
if not exist "%VPY%" goto :err_venv

:install_packages
echo  Installing packages from pypi.org. The first time takes a few minutes...
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -r "%REQ%"
if errorlevel 1 goto :err_pip
copy /y "%REQ%" "%VENV%\owt-requirements.txt" >nul

rem ---- NADCON grids --------------------------------------------------------
:grids
set "PROJ_USER_WRITABLE_DIRECTORY=%OWT_HOME%\proj"
if not exist "%PROJ_USER_WRITABLE_DIRECTORY%" mkdir "%PROJ_USER_WRITABLE_DIRECTORY%"
for %%G in (us_noaa_conus.tif us_noaa_nadcon5_nad27_nad83_1986_conus.tif) do (
  if not exist "%PROJ_USER_WRITABLE_DIRECTORY%\%%G" call :download "https://cdn.proj.org/%%G" "%PROJ_USER_WRITABLE_DIRECTORY%\%%G"
)
if not exist "%PROJ_USER_WRITABLE_DIRECTORY%\us_noaa_conus.tif" echo  [WARNING] No NADCON grid. NAD27 shifts can be wrong by a few meters.

rem ---- settings for the app -----------------------------------------------
set "OWT_DATA_DIR=%OWT_HOME%\data"
set "OWT_FRONTEND_DIR=%APP_DIR%frontend"
if "%ANTHROPIC_API_KEY%"=="" if exist "%OWT_HOME%\anthropic_key.txt" set /p ANTHROPIC_API_KEY=<"%OWT_HOME%\anthropic_key.txt"

if "%OWT_SETUP_ONLY%"=="1" (
  echo  Setup complete.
  exit /b 0
)

rem ---- start ---------------------------------------------------------------
echo.
echo  The app starts at %URL%
echo  Your browser opens when the app is ready.
echo  Close this window to stop the app.
echo.
if not "%OWT_NO_BROWSER%"=="1" start "" /b powershell -NoProfile -Command "for ($i = 0; $i -lt 120; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%URL%/api/health' | Out-Null; Start-Process '%URL%'; break } catch { Start-Sleep -Seconds 1 } }"
cd /d "%APP_DIR%backend"
"%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port %OWT_PORT%
echo.
echo  The app stopped. If the port is in use, set OWT_PORT to another port, for example:
echo    set OWT_PORT=8010
echo    Start-OffsetWellWebtool.bat
goto :pause_exit

rem ==========================================================================
rem  Subroutines
rem ==========================================================================

:is_running
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%URL%/api/health' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
exit /b %errorlevel%

:find_python
set "PY="
if exist "%PY_HOME%\python.exe" call :try_python "%PY_HOME%\python.exe"
if defined PY exit /b 0
if "%OWT_FORCE_PYTHON_DOWNLOAD%"=="1" exit /b 0
for %%V in (3.13 3.12 3.11) do (
  if not defined PY call :try_python py -%%V
)
if not defined PY call :try_python python
exit /b 0

:try_python
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set PY=%*
echo  Python found: %*
exit /b 0

:install_python
echo  Python 3.11 or later was not found.
echo  Downloading Python %OWT_PYTHON_VERSION% from python.org (about 30 MB)...
set "PY_SETUP=%TEMP%\owt-python-%OWT_PYTHON_VERSION%-amd64.exe"
call :download "https://www.python.org/ftp/python/%OWT_PYTHON_VERSION%/python-%OWT_PYTHON_VERSION%-amd64.exe" "%PY_SETUP%"
if not exist "%PY_SETUP%" exit /b 0
echo  Installing Python for your user only. No admin rights are needed...
"%PY_SETUP%" /quiet InstallAllUsers=0 TargetDir="%PY_HOME%" PrependPath=0 Include_launcher=0 Include_test=0 Include_doc=0 Include_tcltk=0 Shortcuts=0 AssociateFiles=0
del "%PY_SETUP%" >nul 2>&1
if exist "%PY_HOME%\python.exe" call :try_python "%PY_HOME%\python.exe"
exit /b 0

:download
rem Windows proxy first (PowerShell), then curl.exe with HTTPS_PROXY.
powershell -NoProfile -Command "$ProgressPreference = 'SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $wc = New-Object Net.WebClient; $wc.Proxy = [Net.WebRequest]::GetSystemWebProxy(); $wc.Proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials; $wc.DownloadFile('%~1', '%~2')" >nul 2>&1
if exist "%~2" exit /b 0
curl.exe -fsSL --retry 3 -o "%~2" "%~1" >nul 2>&1
if exist "%~2" exit /b 0
echo  [WARNING] Download failed: %~1
exit /b 1

rem ==========================================================================
rem  Messages
rem ==========================================================================

:already_running
echo  The app is already running. Opening %URL%
start "" "%URL%"
exit /b 0

:err_no_app
echo  [ERROR] The folder "backend" is not next to this file.
echo  Extract the complete ZIP file, then start this file from the extracted folder.
goto :pause_exit

:err_no_python
echo  [ERROR] Python could not be installed.
echo  Install Python 3.11 or later from your company software center,
echo  or from https://www.python.org/downloads/ (select "Install for me only").
echo  Then start this file again.
goto :pause_exit

:err_venv
echo  [ERROR] The Python environment could not be made in:
echo    %VENV%
goto :pause_exit

:err_pip
echo  [ERROR] The packages could not be installed.
echo  If your company network uses a proxy, set it and start this file again:
echo    set HTTPS_PROXY=http://your-proxy:port
echo    Start-OffsetWellWebtool.bat
echo  If the network blocks pypi.org, ask IT to allow pypi.org and files.pythonhosted.org.
goto :pause_exit

:pause_exit
if "%OWT_NO_PAUSE%"=="1" exit /b 1
echo.
pause
exit /b 1
