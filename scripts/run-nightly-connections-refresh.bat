@echo off
setlocal

:: Timestamped log filename (matches existing project batch style).
set TODAY=%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%
set CURRENT_TIME=%TIME:~0,2%%TIME:~3,2%
set CURRENT_TIME=%CURRENT_TIME: =0%
set FILENAME=connections-refresh_%TODAY%_%CURRENT_TIME%.log

:: Project/runtime settings.
set PROJECT_ROOT=C:\python\hierag
set LOG_DIR=%PROJECT_ROOT%\data\logs
set CONDA_ACTIVATE=C:\Users\harvgs-admin\Anaconda3\Scripts\activate.bat
set CONDA_ENV=py313

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Allow custom args when manually invoking the script; use nightly defaults otherwise.
set REFRESH_ARGS=%*
if "%REFRESH_ARGS%"=="" (
    set REFRESH_ARGS=--site-id 2 --max-pages 3000 --prune-missing --prune-missing-after 3 --prune-status-codes 404,410
)

CALL "%CONDA_ACTIVATE%" %CONDA_ENV%
cd /d "%PROJECT_ROOT%"
python -u -m core.daily_connections_refresh %REFRESH_ARGS% > "%LOG_DIR%\%FILENAME%" 2>&1

exit /b %ERRORLEVEL%
