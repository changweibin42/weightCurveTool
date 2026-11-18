@echo off
setlocal
set "WCT_MAYAPY=C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe"
if not defined WCT_PYTHON_DEV set "WCT_PYTHON_DEV=D:\Python3108"
if not exist "%WCT_MAYAPY%" (
  echo ERROR: Edit WCT_MAYAPY in this file to your Maya 2024 mayapy.exe.
  exit /b 1
)
pushd "%~dp0"
"%WCT_MAYAPY%" setup.py build_ext --inplace --force
if errorlevel 1 goto :failed
"%WCT_MAYAPY%" verify_native.py
if errorlevel 1 goto :failed
"%WCT_MAYAPY%" benchmark_profiles.py
if errorlevel 1 goto :failed
echo Build and verification passed. See README for the two files to install.
popd
exit /b 0
:failed
echo Build or verification failed. Do not install this build.
popd
exit /b 1
