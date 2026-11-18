"""Run via Maya2024/bin/mayapy.exe from the VS 2022 x64 prompt."""
import os
from pathlib import Path
import struct
import sys
from setuptools import Extension,setup
from Cython.Build import cythonize

ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
include_dirs=[];library_dirs=[];libraries=[]
if os.name=='nt':
    if sys.version_info[:2]!=(3,10) or struct.calcsize('P')!=8:
        raise RuntimeError('Build this Maya 2024 package with 64-bit Python 3.10 (mayapy.exe).')
    dev=Path(os.environ.get('WCT_PYTHON_DEV',r'D:\Python3108'))
    tag='{}{}'.format(sys.version_info.major,sys.version_info.minor)
    if not (dev/'include/Python.h').is_file() or not (dev/'libs'/('python'+tag+'.lib')).is_file():
        raise RuntimeError('Missing Python development files. Set WCT_PYTHON_DEV to your Python 3.10 x64 installation.')
    include_dirs=[str(dev/'include')];library_dirs=[str(dev/'libs')];libraries=['python'+tag]
    flags=['/O2','/fp:precise']
else:
    # For local source validation only. Linux binaries are not delivered to Maya/Windows.
    flags=['-O3','-ffp-contract=off','-fno-fast-math']

extension=Extension('weightCurveTool.tools._wct_profiles_native',
    ['weightCurveTool/tools/_wct_profiles_native.pyx'],language='c++',
    include_dirs=include_dirs,library_dirs=library_dirs,libraries=libraries,
    extra_compile_args=flags)
setup(name='wct-projection-profiles-native',version='17.7.0',
      ext_modules=cythonize([extension],compiler_directives={'language_level':3}))
