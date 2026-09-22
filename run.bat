@echo off
rem ===========================================================================
rem  JobRadar - local launcher for Windows
rem
rem  Double-click to run, or from a terminal:
rem
rem    run.bat                    http://127.0.0.1:8000 with auto-reload
rem    run.bat --open             ... and open the browser for you
rem    run.bat --port 8080        listen on another port
rem    run.bat --host 0.0.0.0     bind a specific address
rem    run.bat --lan              shorthand for --host 0.0.0.0 (share on LAN)
rem    run.bat --sqlite           ignore MYSQL_* in .env, use ./jobradar.db
rem    run.bat --no-reload        do not restart when files change
rem    run.bat --install          force-reinstall dependencies, then run
rem    run.bat --help             show this help
rem
rem  On first run it creates .venv, installs requirements.txt and copies
rem  .env.example to .env. Later runs only reinstall when requirements.txt
rem  changes. Press Ctrl+C to stop the server.
rem ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "VENV=.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "STAMP=%VENV%\.requirements.sha256"

set "HOST=127.0.0.1"
set "PORT=8000"
set "RELOAD=--reload"
set "FORCE_INSTALL="
set "OPEN_BROWSER="
set "USE_SQLITE="

rem ---------------------------------------------------------------- arguments
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--port"      goto opt_port
if /i "%~1"=="--host"      goto opt_host
if /i "%~1"=="--lan"       goto opt_lan
if /i "%~1"=="--sqlite"    goto opt_sqlite
if /i "%~1"=="--no-reload" goto opt_noreload
if /i "%~1"=="--install"   goto opt_install
if /i "%~1"=="--open"      goto opt_open
if /i "%~1"=="--help"      goto usage
if /i "%~1"=="-h"          goto usage
if /i "%~1"=="/?"          goto usage
echo [jobradar] Unknown option: %~1
echo.
goto usage

:opt_port
if "%~2"=="" (
    echo [jobradar] --port needs a number, e.g. --port 8080
    goto fail
)
set "PORT=%~2"
shift
shift
goto parse

:opt_host
if "%~2"=="" (
    echo [jobradar] --host needs an address, e.g. --host 0.0.0.0
    goto fail
)
set "HOST=%~2"
shift
shift
goto parse

:opt_lan
set "HOST=0.0.0.0"
shift
goto parse

:opt_sqlite
set "USE_SQLITE=1"
shift
goto parse

:opt_noreload
set "RELOAD="
shift
goto parse

:opt_install
set "FORCE_INSTALL=1"
shift
goto parse

:opt_open
set "OPEN_BROWSER=1"
shift
goto parse

:parsed

rem ------------------------------------------------------- virtual environment
if exist "%VPY%" goto venv_ready

echo [jobradar] No virtual environment yet - creating %VENV% ...

set "SYSPY="
where py >nul 2>nul
if not errorlevel 1 set "SYSPY=py -3"
if not defined SYSPY (
    where python >nul 2>nul
    if not errorlevel 1 set "SYSPY=python"
)
if not defined SYSPY (
    echo.
    echo [jobradar] ERROR: Python was not found on PATH.
    echo            Install Python 3.11 or newer from https://www.python.org/downloads/
    echo            and tick "Add python.exe to PATH" in the installer.
    goto fail
)

%SYSPY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo.
    echo [jobradar] ERROR: JobRadar needs Python 3.11 or newer. Found:
    %SYSPY% --version
    goto fail
)

%SYSPY% -m venv "%VENV%"
if not exist "%VPY%" (
    echo.
    echo [jobradar] ERROR: could not create the virtual environment in %VENV%.
    goto fail
)

:venv_ready

rem ------------------------------------------------------------- dependencies
set "REQHASH="
for /f "skip=1 delims=" %%H in ('certutil -hashfile requirements.txt SHA256 2^>nul') do (
    if not defined REQHASH set "REQHASH=%%H"
)
if not defined REQHASH set "REQHASH=unknown"
set "REQHASH=%REQHASH: =%"

set "OLDHASH="
if exist "%STAMP%" set /p OLDHASH=<"%STAMP%"
if not exist "%VENV%\Scripts\uvicorn.exe" set "OLDHASH=__missing__"
if defined FORCE_INSTALL set "OLDHASH=__forced__"

if "!OLDHASH!"=="!REQHASH!" goto deps_ready

echo [jobradar] Installing dependencies from requirements.txt (the first run takes a minute)...
"%VPY%" -m pip install --disable-pip-version-check --quiet --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo [jobradar] ERROR: pip install failed. Check your internet connection / proxy,
    echo            then run "run.bat --install" to try again.
    goto fail
)
> "%STAMP%" echo !REQHASH!
echo [jobradar] Dependencies ready.

:deps_ready

rem --------------------------------------------------------------------- .env
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [jobradar] Created .env from .env.example.
        echo            The app runs without any keys - add OPENAI_API_KEY for AI
        echo            scoring and TELEGRAM_* for alerts whenever you like.
    ) else (
        echo [jobradar] No .env found - continuing with defaults.
    )
)

rem Read the database settings out of .env (batch, so we can warn before start).
set "CFG_DATABASE_URL="
set "CFG_MYSQL_HOST="
set "CFG_MYSQL_PORT="
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if /i "%%~A"=="DATABASE_URL" set "CFG_DATABASE_URL=%%~B"
        if /i "%%~A"=="MYSQL_HOST"   set "CFG_MYSQL_HOST=%%~B"
        if /i "%%~A"=="MYSQL_PORT"   set "CFG_MYSQL_PORT=%%~B"
    )
)
if not defined CFG_MYSQL_PORT set "CFG_MYSQL_PORT=3306"

if defined USE_SQLITE goto force_sqlite
if defined CFG_DATABASE_URL goto db_ready
if not defined CFG_MYSQL_HOST goto db_ready

rem MySQL is configured - make sure it is actually up before uvicorn tries.
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient;try{$ok=$c.ConnectAsync('!CFG_MYSQL_HOST!',!CFG_MYSQL_PORT!).Wait(2500)}catch{$ok=$false};if($ok){exit 0}else{exit 1}" >nul 2>nul
if not errorlevel 1 goto db_ready

echo.
echo [jobradar] .env points at MySQL on !CFG_MYSQL_HOST!:!CFG_MYSQL_PORT!, but nothing is
echo            listening there. Start your MySQL/MariaDB server, or run JobRadar
echo            on the local SQLite file instead (jobradar.db in this folder).
echo.
choice /c YN /n /t 20 /d Y /m "Use the local SQLite database instead? [Y/n] "
if errorlevel 2 (
    echo.
    echo [jobradar] Aborted. Start MySQL and run run.bat again, or use: run.bat --sqlite
    goto fail
)
echo.

:force_sqlite
rem DATABASE_URL wins over .env: load_dotenv() never overrides a real env var.
set "SQLITE_PATH=%CD%\jobradar.db"
set "SQLITE_PATH=!SQLITE_PATH:\=/!"
set "DATABASE_URL=sqlite:///!SQLITE_PATH!"
echo [jobradar] Database: SQLite - %CD%\jobradar.db

:db_ready

if "%HOST%"=="0.0.0.0" (
    findstr /b /r /c:"JOBRADAR_ACCESS_KEY=..*" .env >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [jobradar] WARNING: binding to 0.0.0.0 with no JOBRADAR_ACCESS_KEY in .env -
        echo            anyone who can reach port %PORT% will see your feed and tracker.
        echo.
    )
)

rem ------------------------------------------------------------------ browser
set "URL_HOST=%HOST%"
if "%HOST%"=="0.0.0.0" set "URL_HOST=127.0.0.1"
if defined OPEN_BROWSER start "" /min cmd /c "ping -n 6 127.0.0.1 >nul && start http://!URL_HOST!:%PORT%/"

rem ------------------------------------------------------------------- server
echo.
echo [jobradar] Starting on http://%URL_HOST%:%PORT%/   (Ctrl+C to stop)
echo.
"%VPY%" -m uvicorn app.main:app --host %HOST% --port %PORT% %RELOAD%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [jobradar] Server exited with code %RC%.
    echo            Port already in use?  try:  run.bat --port 8001
    echo            Database unreachable? try:  run.bat --sqlite
    goto fail
)
endlocal
exit /b 0

rem --------------------------------------------------------------------- help
:usage
echo JobRadar launcher
echo.
echo   run.bat [--host ADDR] [--port N] [--lan] [--sqlite]
echo           [--no-reload] [--install] [--open]
echo.
echo   --port N       port to listen on            (default 8000)
echo   --host ADDR    address to bind              (default 127.0.0.1)
echo   --lan          same as --host 0.0.0.0, shares the app on your network
echo   --sqlite       ignore MYSQL_* in .env and use the local jobradar.db
echo   --no-reload    do not restart on file changes
echo   --install      force-reinstall dependencies before starting
echo   --open         open the browser once the server is up
echo   --help         show this message
echo.
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
