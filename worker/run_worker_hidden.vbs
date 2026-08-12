' Launch the Quran Studio worker with no visible console window.
'
' Task Scheduler runs tasks in the user's interactive session, so a cmd.exe
' action pops a console window on every logon. WScript.Shell.Run with
' intWindowStyle=0 starts the same batch file hidden.
'
' Deliberately NOT solved with "run whether user is logged on or not" (S4U):
' that moves the worker to a non-interactive session, where Vulkan
' (Real-ESRGAN) and NVENC are unreliable. Keeping the interactive session
' preserves exactly the GPU behaviour that was verified working.
'
' stdout/stderr are appended to worker\worker.log — that file is the only way
' to see what a hidden worker is doing.

Option Explicit

Dim shell, fso, scriptDir, repoRoot, batPath, logPath, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoRoot = fso.GetParentFolderName(scriptDir)
batPath = fso.BuildPath(scriptDir, "run_worker.bat")
logPath = fso.BuildPath(scriptDir, "worker.log")

shell.CurrentDirectory = repoRoot

' cmd /c is needed for the >> redirection.
command = "cmd.exe /c """"" & batPath & """ >> """ & logPath & """ 2>&1"""

' 0 = hidden window, False = do not wait for it to exit.
shell.Run command, 0, False
