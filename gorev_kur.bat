@echo off
chcp 65001 >nul
rem Bilgisayar gorevini kurar: Windows her acildiginda (oturum acilinca) bilgisayar_gorevi.vbs bir kez calisir.
rem Yonetici izni gerekmez; Baslangic klasorune kucuk bir betik yazilir. Kaldirmak icin gorev_kaldir.bat.
cd /d %~dp0
if not exist .env (
  echo .env dosyasi yok. Once HEDEF_DB_URL ve API_FOOTBALL_KEY ayarlarini yazin, bkz. KURULUM.md.
  pause
  goto :eof
)
findstr /b /c:"HEDEF_DB_URL=" .env >nul || (
  echo .env dosyasinda HEDEF_DB_URL yok. Neon baglanti adresini HEDEF_DB_URL=... olarak yazin.
  pause
  goto :eof
)
set "HEDEF=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FutbolSonucTahmin_gorev.vbs"
> "%HEDEF%" echo CreateObject("WScript.Shell").Run "wscript ""%~dp0bilgisayar_gorevi.vbs""", 0, False
echo Gorev kuruldu. Windows her acildiginda yaklasik 1,5 dakika sonra calisir. Kayit: data\gorev.log
pause
