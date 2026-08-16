' Voice Assistant launcher — starts everything with NO console window
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = dir
' start chat-history frontend hidden, then start the desktop-pet GUI hidden
ws.Run "cmd /c start /b python web_server.py > web_server.log 2>&1 & python voice_gui.py", 0, False
