@echo off
rem Arka planda calisan FutbolSonucTahmin sunucusunu durdurur (yerel 8765 portunu dinleyen islem).
rem Windows dilinden bagimsizdir: yalnizca yerel adres sutununa bakar.
set BULUNDU=0
for /f "tokens=2,5" %%a in ('netstat -ano ^| findstr ":8765"') do (
  if "%%a"=="127.0.0.1:8765" (taskkill /PID %%b /F >nul 2>&1 & set BULUNDU=1)
  if "%%a"=="0.0.0.0:8765" (taskkill /PID %%b /F >nul 2>&1 & set BULUNDU=1)
)
if "%BULUNDU%"=="1" (echo Sistem durduruldu.) else (echo Calisan sistem bulunamadi.)
%SystemRoot%\System32\timeout.exe /t 3 >nul
