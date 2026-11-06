@echo off
rem Lightweight entrypoint shim (forum #490) — Windows flavour.
if "%ANNA_NODE_EXECUTABLE%"=="" (
  echo runtime-probe: ANNA_NODE_EXECUTABLE is not set. 1>&2
  echo This Executa requires the Anna platform Node.js runtime. Upgrade 1>&2
  echo the Matrix Agent to a release providing the shared Node 22 runtime. 1>&2
  exit /b 78
)
if "%EXECUTA_HOME%"=="" (set "EXECUTA_HOME=%~dp0..")
"%ANNA_NODE_EXECUTABLE%" --enable-source-maps "%EXECUTA_HOME%\runtime_probe_plugin.js" %*
