' Voice Assistant launcher - starts watcher (hidden window).
' Watcher brings up DSH desktop WITH LOG (token), then starts the pet.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = dir
PYW = "
C:\Users\YBB\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\pythonw.exe
"
If Not fso.FileExists(PYW) Then PYW = "pythonw.exe"
ws.Run Chr(34) & PYW & Chr(34) & " " & Chr(34) & dir & Chr(92) & "watcher.py" & Chr(34), 0, False
