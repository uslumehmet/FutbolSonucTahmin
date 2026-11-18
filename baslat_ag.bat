@echo off
chcp 65001 >nul
cd /d %~dp0
rem Telefondan erisim icin: arka plandaki sunucu durdurulur, bu pencere acikken agdan erisilebilir.
call "%~dp0durdur.bat" >nul
echo Ayni Wi-Fi'deki telefondan acmak icin asagidaki IPv4 adresini kullanin:
ipconfig | findstr /i "IPv4"
echo Telefonda: http://BU-IP-ADRESI:8765
echo (Windows guvenlik duvari izin isterse "Ozel aglar" icin izin verin.)
echo Bu pencereyi kapatinca sistem durur; arka plana donmek icin baslat.bat'i calistirin.
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist data\bahis.db .venv\Scripts\python -m app.cli init
.venv\Scripts\python -m uvicorn app.web.main:app --host 0.0.0.0 --port 8765
