' FutbolSonucTahmin bilgisayar gorevi: Windows acilinca bir kez calisir, pencere acmaz.
' Buluttaki veritabanina baglanir (.env: HEDEF_DB_URL), API-Football'dan maclari, oranlari ve eksikleri
' ev internetinden alir, kuponlari kurar ve kapanir. Render'daki site ayni veritabanini okur.
' Kayit: data\gorev.log. Kurmak icin gorev_kur.bat, kaldirmak icin gorev_kaldir.bat.
Option Explicit
Dim sh, fso, dir

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

' Acilista internet baglantisi biraz gec gelir.
WScript.Sleep 90000

If fso.FileExists(dir & "\.venv\Scripts\python.exe") And fso.FileExists(dir & "\.env") Then
  If Not fso.FolderExists(dir & "\data") Then fso.CreateFolder(dir & "\data")
  sh.Run "cmd /c set PYTHONIOENCODING=utf-8&& .venv\Scripts\python.exe -m app.cli bulut-guncelle >> data\gorev.log 2>&1", 0, False
End If
