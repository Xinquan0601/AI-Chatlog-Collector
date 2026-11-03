@echo off
setlocal
cd /d "%~dp0.."
if not "%AGENTTRAIL_PYTHON%"=="" (
  "%AGENTTRAIL_PYTHON%" app.py %*
  goto :end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python app.py %*
  goto :end
)
where py >nul 2>nul
if %errorlevel%==0 (
  py app.py %*
  goto :end
)
echo Python 3.11+ was not found. Install it or set AGENTTRAIL_PYTHON.
exit /b 1
:end
endlocal
