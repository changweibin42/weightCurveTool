@echo off
setlocal
cd /d "%~dp0" || exit /b 1
for %%I in ("%~dp0..\..") do set "WCT_SOURCE_ROOT=%%~fI"
if not defined WCT_INSTALL_ROOT set "WCT_INSTALL_ROOT=%WCT_SOURCE_ROOT%"
for %%I in ("%WCT_INSTALL_ROOT%\.") do set "WCT_INSTALL_ROOT=%%~fI"
if not exist "%WCT_INSTALL_ROOT%\__main__.py" (
 echo Tool installation not found: %WCT_INSTALL_ROOT%
 exit /b 1
)
if not exist "%WCT_INSTALL_ROOT%\tools\." (
 echo Tools directory not found in "%WCT_INSTALL_ROOT%".
 exit /b 1
)
if not exist "dist\wctSurfaceCurve.mll" (
 echo Build the plugin first. No binary is included in this package.
 exit /b 1
)
if not exist "dist\.wct_build_1_8_2.ok" (
 echo Run build_maya2024.cmd successfully before installing this version.
 exit /b 1
)
for %%F in (NativeEdgeCuts.py NativeQueryReuse.py) do (
 if not exist "%WCT_SOURCE_ROOT%\tools\%%F" (
  echo Missing runtime file "%WCT_SOURCE_ROOT%\tools\%%F".
  exit /b 1
 )
)
echo Close Maya before installing. Existing first backups are preserved.
if not exist "%WCT_INSTALL_ROOT%\plug-ins\." (
 mkdir "%WCT_INSTALL_ROOT%\plug-ins" || exit /b 1
)
if exist "%WCT_INSTALL_ROOT%\plug-ins\wctSurfaceCurve.mll" if not exist "%WCT_INSTALL_ROOT%\plug-ins\wctSurfaceCurve.pre_1_8_2.mll.bak" (
 copy /Y "%WCT_INSTALL_ROOT%\plug-ins\wctSurfaceCurve.mll" "%WCT_INSTALL_ROOT%\plug-ins\wctSurfaceCurve.pre_1_8_2.mll.bak" >nul
 if errorlevel 1 exit /b 1
)
copy /Y "dist\wctSurfaceCurve.mll" "%WCT_INSTALL_ROOT%\plug-ins\wctSurfaceCurve.mll" >nul
if errorlevel 1 (
 echo Plugin copy failed. Close Maya and try again.
 exit /b 1
)
rem In a clone the Python tools are already in their installation location.
if /I "%WCT_SOURCE_ROOT%"=="%WCT_INSTALL_ROOT%" goto installed
for %%F in (NativeEdgeCuts.py NativeQueryReuse.py) do (
 if exist "%WCT_INSTALL_ROOT%\tools\%%F" if not exist "%WCT_INSTALL_ROOT%\tools\%%F.pre_17_9_2.bak" (
  copy /Y "%WCT_INSTALL_ROOT%\tools\%%F" "%WCT_INSTALL_ROOT%\tools\%%F.pre_17_9_2.bak" >nul
  if errorlevel 1 exit /b 1
 )
 copy /Y "%WCT_SOURCE_ROOT%\tools\%%F" "%WCT_INSTALL_ROOT%\tools\%%F" >nul
 if errorlevel 1 exit /b 1
)
:installed
echo Installed. Restart Maya, open the tool, create/activate three curves,
echo then run python\maya_audit_strict_boundary_queries.py
echo then python\maya_audit_native_projection.py
echo and python\maya_audit_query_reuse.py.
endlocal
exit /b 0
