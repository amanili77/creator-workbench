Option Explicit
Dim shell, fso, baseDir, scriptPath, candidates, pythonw, pythonArgs, i
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(baseDir, "workbench.py")
candidates = Array( _
  fso.BuildPath(baseDir, "runtime\Scripts\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%WINDIR%\pyw.exe"), _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe") _
)
pythonw = ""
pythonArgs = ""
For i = 0 To UBound(candidates)
  If fso.FileExists(candidates(i)) Then pythonw = candidates(i): Exit For
Next
If Not fso.FileExists(scriptPath) Then
  MsgBox "workbench.py was not found. Please keep the complete folder.", 16, "Creator Workbench"
  WScript.Quit 1
End If
If pythonw = "" Then
  MsgBox "Python was not found. Please run 首次安装.bat first.", 48, "Creator Workbench"
  WScript.Quit 2
End If
If LCase(fso.GetFileName(pythonw)) = "pyw.exe" Then pythonArgs = "-3 "
shell.CurrentDirectory = baseDir
shell.Run """" & pythonw & """ " & pythonArgs & """" & scriptPath & """", 0, False
