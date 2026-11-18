@echo off
setlocal
cd /d "%~dp0"

if not exist "3x-ui.exe" (
    echo [ERROR] 3x-ui.exe was not found.
    echo Extract the complete OMID-3x-ui ZIP before running this file.
    pause
    exit /b 1
)

if not exist "bin\xray-windows-amd64.exe" (
    echo [ERROR] bin\xray-windows-amd64.exe was not found.
    echo The Windows package may be incomplete. Re-download and extract it.
    pause
    exit /b 1
)

rem Use the bin directory beside this launcher, regardless of the current shell directory.
set "XUI_BIN_FOLDER=%CD%\bin"

echo ========================================
echo          OMID-3x-ui for Windows
echo ========================================
echo.
echo Starting the panel...
echo Default panel address: http://127.0.0.1:2053/
echo Keep this window open while using the panel.
echo Press Ctrl+C here to stop the panel.
echo.

"%~dp03x-ui.exe"
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" goto launch_error

:normal_exit
echo.
echo OMID-3x-ui has stopped.
pause
exit /b 0

:launch_error
echo.
echo [ERROR] OMID-3x-ui exited with code %APP_EXIT_CODE%.
echo Review the messages above for details.
pause
exit /b %APP_EXIT_CODE%
