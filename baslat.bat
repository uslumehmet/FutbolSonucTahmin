@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv (
  echo Ilk kurulum: Python ortami hazirlaniyor...
  python -m venv .venv || goto :hata
  .venv\Scripts\python -m pip install -r requirements.txt || goto :hata
)
if not exist data\bahis.db (
  echo Ilk kurulum: gecmis mac verisi indiriliyor...
  .venv\Scripts\python -m app.cli init || goto :hata
)
rem Sunucu arka planda calisir (pencere acmaz); zaten calisiyorsa yeniden baslatmaz.
wscript "%~dp0arka_plan.vbs"
echo Sistem baslatiliyor, lutfen bekleyin...
for /l %%i in (1,1,60) do (
  %SystemRoot%\System32\curl.exe -s -o nul http://127.0.0.1:8765/static/app.css && goto :hazir
  %SystemRoot%\System32\timeout.exe /t 1 /nobreak >nul
)
echo Sunucu 60 saniyede acilmadi. data\sunucu.log dosyasina bakin.
pause
goto :eof
:hazir
start "" http://localhost:8765
goto :eof
:hata
echo Bir hata olustu. Yukaridaki mesaji kontrol edin.
pause
