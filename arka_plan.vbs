' FutbolSonucTahmin sunucusunu pencere acmadan arka planda baslatir.
' Sunucu zaten calisiyorsa hicbir sey yapmaz.
Option Explicit
Dim sh, fso, dir, http, running

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

running = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 2000, 2000, 2000, 2000
http.Open "GET", "http://127.0.0.1:8765/static/app.css?t=" & CStr(Timer), False
http.Send
If Err.Number = 0 Then
  If http.Status = 200 Then running = True
End If
Err.Clear
On Error GoTo 0

If Not running Then
  If fso.FileExists(dir & "\.venv\Scripts\python.exe") And fso.FileExists(dir & "\data\bahis.db") Then
    sh.Run "cmd /c set PYTHONIOENCODING=utf-8&& .venv\Scripts\python.exe -m uvicorn app.web.main:app --host 127.0.0.1 --port 8765 --log-level warning >> data\sunucu.log 2>&1", 0, False
  End If
End If
