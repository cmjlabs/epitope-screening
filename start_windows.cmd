@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 goto failed
)
if not exist ".venv\dependencies-ready" (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  type nul > .venv\dependencies-ready
)
.venv\Scripts\python.exe -m epitope_screening gui
if errorlevel 1 goto failed
exit /b 0
:failed
 echo Startup failed. Install Python 3.11 with Tcl/Tk and the Python launcher, then try again.
 pause
 exit /b 1
