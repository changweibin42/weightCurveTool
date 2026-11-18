@echo off
setlocal
cd /d "%~dp0"
if exist "dist\.wct_build_1_8_2.ok" del /Q "dist\.wct_build_1_8_2.ok"
if not defined MAYA_LOCATION set "MAYA_LOCATION=C:\Program Files\Autodesk\Maya2024"
if not defined MAYA_DEVKIT set "MAYA_DEVKIT=%MAYA_LOCATION%"
where cmake >nul 2>nul
if errorlevel 1 (
 echo CMake was not found. Enable C++ CMake tools in Visual Studio Installer.
 exit /b 1
)
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 "-DMAYA_LOCATION=%MAYA_LOCATION%" "-DMAYA_DEVKIT=%MAYA_DEVKIT%"
if errorlevel 1 exit /b 1
cmake --build build --config Release
if errorlevel 1 exit /b 1
> "dist\.wct_build_1_8_2.ok" echo Built plugin 1.8.2
if errorlevel 1 exit /b 1
echo Built: %CD%\dist\wctSurfaceCurve.mll
echo Run python\maya_smoke_test.py inside Maya before installing the tool overlay.
endlocal
