@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 goto :folder_error
set "KUPOL_PYTHON=.venv\Scripts\python.exe"
if exist "runtime\python\python.exe" goto :portable
if exist ".venv\Scripts\python.exe" goto :check_venv
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto :create_with_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto :create_with_python
echo Не найден Python 3.11 или новее. Установите Python и повторите запуск.
goto :failed

:create_with_py
echo Подготовка локального окружения Python...
py -3 -m venv ".venv"
if errorlevel 1 goto :failed
goto :check_venv

:create_with_python
echo Подготовка локального окружения Python...
python -m venv ".venv"
if errorlevel 1 goto :failed

:check_venv
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -c "import aiohttp; from websockets.asyncio.client import connect; import importlib.metadata as m; assert 15 <= int(m.version('websockets').split('.')[0]) < 18; assert (3,14,4) <= tuple(map(int,m.version('aiohttp').split('.')[:3])) < (4,0,0)" >nul 2>&1
if not errorlevel 1 goto :run
echo Установка зависимостей веб-интерфейса. При первом запуске нужен интернет.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt"
if errorlevel 1 goto :failed

:portable
if not exist "runtime\python\python.exe" goto :run
set "KUPOL_PYTHON=runtime\python\python.exe"
"%KUPOL_PYTHON%" -c "import ssl, sqlite3, aiohttp, websockets; from websockets.asyncio.client import connect" >nul 2>&1
if errorlevel 1 goto :failed

:run
"%KUPOL_PYTHON%" -X utf8 "start_web.py" %*
set "web_exit=%errorlevel%"
echo.
echo Веб-интерфейс остановлен. Код завершения: %web_exit%
pause
popd
exit /b %web_exit%

:failed
echo Подготовка не завершена. Сообщение об ошибке указано выше.
pause
popd
exit /b 2

:folder_error
echo Не удалось открыть каталог программы.
pause
exit /b 2
