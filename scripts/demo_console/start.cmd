@echo off
setlocal

set "launcher=%SENTINELOPS_FAULT_CONSOLE_LAUNCHER%"
if not defined launcher set "launcher=%~dp0..\..\.local-ops\start-fault-console.cmd"

if not exist "%launcher%" (
    >&2 echo Local Fault Console launcher is missing. See scripts/demo_console/START.md.
    exit /b 2
)

if not "%~2"=="" goto :usage
if "%~1"=="" goto :open
if /i not "%~1"=="--no-browser" goto :usage

call "%launcher%" --no-browser
exit /b %errorlevel%

:open
call "%launcher%"
exit /b %errorlevel%

:usage
>&2 echo Usage: scripts\demo_console\start.cmd [--no-browser]
exit /b 64
