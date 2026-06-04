' Row-Bot silent launcher â€“ runs launch_row_bot.bat with no visible console window
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\launch_row_bot.bat" & Chr(34), 0, False
