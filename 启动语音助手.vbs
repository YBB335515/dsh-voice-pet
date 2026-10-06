' Voice Assistant launcher — starts everything with NO console window
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = dir
' start chat-history frontend hidden, then start the desktop-pet GUI hidden
' GUI 的 stdout/stderr 也落盘（原来直接丢弃 → 线程异常完全看不见）
ws.Run "cmd /c start /b python web_server.py > web_server.log 2>&1 & python voice_gui.py >> pet.err.log 2>&1", 0, False
