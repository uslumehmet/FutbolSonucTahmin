@echo off
chcp 65001 >nul
rem Bilgisayar gorevini kaldirir (gorev_kur.bat'in Baslangic klasorune yazdigi betigi siler).
set "HEDEF=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FutbolSonucTahmin_gorev.vbs"
if exist "%HEDEF%" (
  del "%HEDEF%"
  echo Gorev kaldirildi.
) else (
  echo Kurulu gorev yok.
)
pause
