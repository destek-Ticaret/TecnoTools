@echo off
REM TecnoTools admin paneli baslatici
REM Backend (port 8000) ve frontend (port 5500) sunucularini ayri pencerelerde calistirir,
REM ardindan tarayicida admin.html sayfasini acar.

cd /d "%~dp0"

echo Backend baslatiliyor (port 8000)...
start "TecnoTools Backend" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\activate && uvicorn app.main:app --reload --port 8000"

echo Frontend baslatiliyor (port 5500)...
start "TecnoTools Frontend" cmd /k "cd /d "%~dp0" && python serve_frontend.py 5500"

echo Sunucularin acilmasi icin 3 saniye bekleniyor...
timeout /t 3 /nobreak >nul

echo Tarayici aciliyor: http://127.0.0.1:5500/admin.html
start "" "http://127.0.0.1:5500/admin.html"

echo.
echo Hazir. Sunuculari kapatmak icin acilan iki pencereyi kapatabilirsin.
exit
