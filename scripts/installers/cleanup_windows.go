package main

import (
	"encoding/binary"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"unicode/utf16"
)

func init() {
	cleanupWindows = recycleSetup
	first := true
	writeSetupProgress = func(line string) {
		words := utf16.Encode([]rune(line + "\r\n"))
		// nsExec's Unicode output detection expects a UTF-16LE signature.
		if first {
			words = append([]uint16{0xfeff}, words...)
			first = false
		}
		data := make([]byte, 2*len(words))
		for index, word := range words {
			binary.LittleEndian.PutUint16(data[2*index:], word)
		}
		_, _ = os.Stdout.Write(data)
	}
}

// Windows keeps the setup executable locked until the user closes the wizard.
// The system shell waits for that exact process and uses the shell Recycle Bin.
func recycleSetup(pid, path string) error {
	const script = `
try {
    $file = Get-Item -LiteralPath $env:COMMUNITY_STORE_SETUP_FILE -ErrorAction Stop
    $process = Get-Process -Id ([int]$env:COMMUNITY_STORE_SETUP_PID) -ErrorAction Stop
    if ($file.PSIsContainer -or $file.Extension -ne '.exe' -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $process.Path -ne $file.FullName) { return }
    if (([IO.DriveInfo]::new($file.PSDrive.Root)).DriveType -ne [IO.DriveType]::Fixed) { return }
    if (!$process.WaitForExit(1800000)) { return }
    $current = Get-Item -LiteralPath $file.FullName -ErrorAction Stop
    if ($current.Length -ne $file.Length -or $current.LastWriteTimeUtc -ne $file.LastWriteTimeUtc -or ($current.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return }
    Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($file.FullName, 'OnlyErrorDialogs', 'SendToRecycleBin')
} catch { exit 1 }
`
	powerShell := filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	command := exec.Command(powerShell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script)
	command.Dir = os.TempDir()
	command.Env = append(os.Environ(), "COMMUNITY_STORE_SETUP_PID="+pid, "COMMUNITY_STORE_SETUP_FILE="+path)
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	if err := command.Start(); err != nil {
		return err
	}
	return command.Process.Release()
}
