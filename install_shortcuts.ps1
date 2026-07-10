$ErrorActionPreference = "Stop"

$appName = "Multicam Capture"
$folderName = "MulticamCapture"
$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDir = Join-Path $env:LOCALAPPDATA "Programs\$folderName"
$exeName = "MulticamCapture.exe"

function Get-FullPath([string]$path) {
    return [System.IO.Path]::GetFullPath($path).TrimEnd('\')
}

function New-AppShortcut([string]$shortcutPath, [string]$targetPath) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetPath
    $shortcut.WorkingDirectory = Split-Path -Parent $targetPath
    $shortcut.IconLocation = "$targetPath,0"
    $shortcut.Description = "Launch $appName"
    $shortcut.Save()
}

function Try-PinTaskbar([string]$shortcutPath) {
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = Split-Path -Parent $shortcutPath
        $name = Split-Path -Leaf $shortcutPath
        $item = $shell.Namespace($folder).ParseName($name)
        if ($null -eq $item) {
            return $false
        }

        foreach ($verb in $item.Verbs()) {
            $verbName = ($verb.Name -replace "&", "")
            if ($verbName -match "Pin to taskbar") {
                $verb.DoIt()
                return $true
            }
        }
    } catch {
        return $false
    }
    return $false
}

$sourceFull = Get-FullPath $sourceDir
$installFull = Get-FullPath $installDir

if ($sourceFull -ne $installFull) {
    if (Test-Path $installDir) {
        Remove-Item $installDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $installDir | Out-Null
    Copy-Item -Path (Join-Path $sourceDir "*") -Destination $installDir -Recurse -Force
}

$exePath = Join-Path $installDir $exeName
if (-not (Test-Path $exePath)) {
    throw "Could not find $exeName in $installDir"
}

$desktopDir = [Environment]::GetFolderPath("Desktop")
$programsDir = [Environment]::GetFolderPath("Programs")
$startMenuDir = Join-Path $programsDir $appName
New-Item -ItemType Directory -Path $startMenuDir -Force | Out-Null

$desktopShortcut = Join-Path $desktopDir "$appName.lnk"
$startShortcut = Join-Path $startMenuDir "$appName.lnk"

New-AppShortcut $desktopShortcut $exePath
New-AppShortcut $startShortcut $exePath

$pinned = Try-PinTaskbar $startShortcut

Write-Host "Installed app folder: $installDir"
Write-Host "Desktop shortcut: $desktopShortcut"
Write-Host "Start Menu shortcut: $startShortcut"
if ($pinned) {
    Write-Host "Taskbar pin: created"
} else {
    Write-Host "Taskbar pin: Windows did not allow automatic pinning."
    Write-Host "You can now right-click the Start Menu shortcut and choose 'Pin to taskbar'."
}
