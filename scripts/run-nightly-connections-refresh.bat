@echo off
setlocal EnableDelayedExpansion

:: Timestamped log filename (matches existing project batch style).
set TODAY=%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%
set CURRENT_TIME=%TIME:~0,2%%TIME:~3,2%
set CURRENT_TIME=%CURRENT_TIME: =0%
set CURRENT_SEC=%TIME:~6,2%
set CURRENT_SEC=%CURRENT_SEC: =0%
set DAYSTAMP=%DATE:~10,4%%DATE:~4,2%%DATE:~7,2%
set SNAPSHOT_STAMP=%DAYSTAMP%-%CURRENT_TIME%%CURRENT_SEC%
set FILENAME=connections-refresh_%TODAY%_%CURRENT_TIME%.log

:: Project/runtime settings.
set PROJECT_ROOT=C:\python\hierag
set LOG_DIR=%PROJECT_ROOT%\data\logs
set SNAPSHOT_DIR=%PROJECT_ROOT%\data\snapshots\nightly
set CONDA_ACTIVATE=C:\Users\harvgs-admin\Anaconda3\Scripts\activate.bat
set CONDA_ENV=py313
set SNAPSHOT_RETENTION_DAYS=14

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%SNAPSHOT_DIR%" mkdir "%SNAPSHOT_DIR%"

set LOG_PATH=%LOG_DIR%\%FILENAME%
echo [%DATE% %TIME%] Starting nightly refresh > "%LOG_PATH%"

:: Allow custom args when manually invoking the script; use nightly defaults otherwise.
set REFRESH_ARGS=%*
if "%REFRESH_ARGS%"=="" (
    set REFRESH_ARGS=--site-id 2 --max-pages 3000 --prune-missing --prune-missing-after 1 --prune-status-codes 404,410
)

call :SnapshotDb "app_runtime"
call :SnapshotDb "scraper"

set SNAPSHOT_BEFORE=0
set SNAPSHOT_AFTER=0
for /f %%C in ('dir /b /a-d "%SNAPSHOT_DIR%\*.db" 2^>nul ^| find /c /v ""') do set SNAPSHOT_BEFORE=%%C
forfiles /p "%SNAPSHOT_DIR%" /m *.db /d -%SNAPSHOT_RETENTION_DAYS% /c "cmd /c del /q @path" >nul 2>&1
for /f %%C in ('dir /b /a-d "%SNAPSHOT_DIR%\*.db" 2^>nul ^| find /c /v ""') do set SNAPSHOT_AFTER=%%C
set /a SNAPSHOT_REMOVED=SNAPSHOT_BEFORE-SNAPSHOT_AFTER
if !SNAPSHOT_REMOVED! LSS 0 set SNAPSHOT_REMOVED=0
echo [%DATE% %TIME%] Snapshot retention cleanup: removed=!SNAPSHOT_REMOVED! cutoff_days=%SNAPSHOT_RETENTION_DAYS%>> "%LOG_PATH%"

CALL "%CONDA_ACTIVATE%" %CONDA_ENV%
cd /d "%PROJECT_ROOT%"
echo [%DATE% %TIME%] python -u -m core.daily_connections_refresh %REFRESH_ARGS%>> "%LOG_PATH%"
python -u -m core.daily_connections_refresh %REFRESH_ARGS% >> "%LOG_PATH%" 2>&1

set EXIT_CODE=%ERRORLEVEL%
echo [%DATE% %TIME%] Completed with exit code %EXIT_CODE%. Log: %LOG_PATH%>> "%LOG_PATH%"

exit /b %EXIT_CODE%

:SnapshotDb
set DB_BASENAME=%~1
set SOURCE_DB=%PROJECT_ROOT%\data\%DB_BASENAME%.db
if not exist "%SOURCE_DB%" (
    echo [%DATE% %TIME%] Snapshot warning: source DB not found: %SOURCE_DB%>> "%LOG_PATH%"
    goto :eof
)

set DEST_DB=%SNAPSHOT_DIR%\%DB_BASENAME%-%SNAPSHOT_STAMP%.db
copy /y "%SOURCE_DB%" "%DEST_DB%" >nul
if errorlevel 1 (
    echo [%DATE% %TIME%] Snapshot warning: failed to copy %SOURCE_DB% to %DEST_DB%>> "%LOG_PATH%"
) else (
    echo [%DATE% %TIME%] Snapshot created: %DEST_DB%>> "%LOG_PATH%"
)
goto :eof
