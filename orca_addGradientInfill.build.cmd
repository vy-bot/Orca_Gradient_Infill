@echo off

:: Get the name of the script without the .cmd extension and remove '.build' if it exists
set SCRIPT_NAME=%~n0
set SCRIPT_NAME=%SCRIPT_NAME:.build=%
set EXE_NAME=%SCRIPT_NAME%.exe
set VENV_DIR=env

echo Cleaning up...
rmdir /s /q %VENV_DIR%
rmdir /s /q build
rmdir /s /q dist
del %SCRIPT_NAME%.spec

echo Creating virtual environment...
python -m venv %VENV_DIR%
if %errorlevel% neq 0 (
    echo An error occurred while creating the virtual environment. Exiting...
    exit /b %errorlevel%
)

echo Activating virtual environment...
call %VENV_DIR%\Scripts\activate
if %errorlevel% neq 0 (
    echo An error occurred while activating the virtual environment. Exiting...
    exit /b %errorlevel%
)

:: Embed Python script in CMD and execute it
echo. > %TEMP%\check_imports_temp.py
echo import importlib > %TEMP%\check_imports_temp.py
echo import subprocess >> %TEMP%\check_imports_temp.py
echo import sys >> %TEMP%\check_imports_temp.py
echo missing_modules = [] >> %TEMP%\check_imports_temp.py
echo with open(r"%SCRIPT_NAME%.py", "r") as file: >> %TEMP%\check_imports_temp.py
echo.    lines = file.readlines() >> %TEMP%\check_imports_temp.py
echo imports = [line.strip() for line in lines if line.strip().startswith("import") or line.strip().startswith("from")] >> %TEMP%\check_imports_temp.py
echo for imp in imports: >> %TEMP%\check_imports_temp.py
echo.    module_name = imp.split()[1] >> %TEMP%\check_imports_temp.py
echo.    if "." in module_name: module_name = module_name.split('.')[0] >> %TEMP%\check_imports_temp.py
echo.    try: >> %TEMP%\check_imports_temp.py
echo.        importlib.import_module(module_name) >> %TEMP%\check_imports_temp.py
echo.        print(f"Module '{module_name}' is already installed.") >> %TEMP%\check_imports_temp.py
echo.    except ImportError: >> %TEMP%\check_imports_temp.py
echo.        print(f"Module '{module_name}' is not installed.") >> %TEMP%\check_imports_temp.py
echo.        missing_modules.append(module_name) >> %TEMP%\check_imports_temp.py
echo if missing_modules: >> %TEMP%\check_imports_temp.py
echo.    print("\nThe following modules are missing and will be installed:") >> %TEMP%\check_imports_temp.py
echo.    for module in missing_modules: >> %TEMP%\check_imports_temp.py
echo.        print(f" - {module}") >> %TEMP%\check_imports_temp.py
echo.        subprocess.check_call([sys.executable, "-m", "pip", "install", module]) >> %TEMP%\check_imports_temp.py
echo else: >> %TEMP%\check_imports_temp.py
echo.    print("\nAll modules are installed.") >> %TEMP%\check_imports_temp.py

python %TEMP%\check_imports_temp.py
if %errorlevel% neq 0 (
    echo An error occurred while checking imports. Exiting...
    exit /b %errorlevel%
)
del %TEMP%\check_imports_temp.py

echo Installing necessary libraries...
pip install pywifi pyinstaller comtypes
if %errorlevel% neq 0 (
    echo An error occurred while installing libraries. Exiting...
    exit /b %errorlevel%
)

echo Running PyInstaller to generate the executable...
pyinstaller --name=%SCRIPT_NAME% --onefile --noconfirm --clean %SCRIPT_NAME%.py
if %errorlevel% neq 0 (
    echo An error occurred during the build process. Exiting...
    exit /b %errorlevel%
)

echo Moving the executable to the script folder...
move /Y dist\%EXE_NAME% .
if %errorlevel% neq 0 (
    echo An error occurred while moving the executable. Exiting...
    exit /b %errorlevel%
)

echo Cleaning up...
:: rmdir /s /q %VENV_DIR%
rmdir /s /q build
rmdir /s /q dist
del %SCRIPT_NAME%.spec
if %errorlevel% neq 0 (
    echo An error occurred during cleanup. Exiting...
    exit /b %errorlevel%
)

echo Process complete. Executable created as %EXE_NAME% in the script folder.
pause

%EXE_NAME%
