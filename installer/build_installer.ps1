# =============================================================================
# build_installer.ps1
# Builds Row-Bot.exe (MSVC), downloads embedded Python + get-pip.py, bundles
# tkinter, pre-installs all pip packages, stages the app with deterministic
# .pyc files, then compiles a self-contained per-user Inno Setup installer.
#
# The installer lays out %LOCALAPPDATA%\Programs\Row-Bot\versions\<version>\
# {app,python} with Row-Bot.exe and state.json at the root (see
# src/row_bot/install_layout.py).
#
# The resulting .exe contains everything needed â€” no internet downloads at
# install time. Ollama remains optional. Playwright Chromium is not bundled,
# as on macOS and Linux: Browser Automation uses installed Chrome or Edge, and
# the user's explicit Install or Repair action downloads the matching Chromium
# into the data folder. -BundlePlaywright (or BUNDLE_PLAYWRIGHT=1) bundles it
# for a local build.
#
# TTS: Kokoro TTS is a pip package â€” no binary to bundle.  The model
#      downloads automatically on first use (~170 MB).
#
# Usage:  .\installer\build_installer.ps1
# =============================================================================

param(
    [string]$PythonVersion = "3.13.2",
    [switch]$SkipDownloads,
    [switch]$BundlePlaywright
)

$ErrorActionPreference = "Stop"
$BuildDir = Join-Path $PSScriptRoot "build"
$ProjectRoot = Split-Path $PSScriptRoot
# Nothing run during the build writes timestamp .pyc files into the payload;
# the deterministic ones are compiled once the payload is complete.
$env:PYTHONDONTWRITEBYTECODE = "1"
$VersionFile = Join-Path $ProjectRoot "src\row_bot\version.py"
$ClientBuild = Join-Path $ProjectRoot "frontend\dist"
$ClientNode = (Get-Command node -ErrorAction Stop).Source
if ((& $ClientNode --version) -ne "v24.15.0") {
    throw "Client packaging requires Node 24.15.0. Build frontend assets before running the installer builder."
}
foreach ($clientFile in @("index.html", "asset-manifest.json", ".vite\manifest.json")) {
    if (!(Test-Path -LiteralPath (Join-Path $ClientBuild $clientFile) -PathType Leaf)) {
        throw "Client build missing. Run the locked frontend build and package step before building an installer."
    }
}
$RowBotVersion = if (Test-Path $VersionFile) {
    $versionLine = Select-String -Path $VersionFile -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($versionLine -and $versionLine.Matches.Count -gt 0) { $versionLine.Matches[0].Groups[1].Value } else { "unknown" }
} else { "unknown" }

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Row-Bot v$RowBotVersion Installer Builder"       -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

function Build-RowBotLauncher {
    # Row-Bot.exe: installer\windows\row_bot_launcher.c with its icon, manifest and version; and
    # Row-Bot Server.exe (row_bot_server.c), which runs the server so Windows names Row-Bot (B355).
    param([string]$Version, [string]$OutDir)

    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (!(Test-Path $vswhere)) {
        throw "Visual Studio Build Tools with the C++ workload are required to build Row-Bot.exe (vswhere.exe not found)."
    }
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (!$vsPath) {
        throw "Visual Studio Build Tools with the C++ x64 tools are required to build Row-Bot.exe."
    }
    $vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"

    # FILEVERSION needs four numbers: 5.2.0-beta.1 -> 5,2,0,0.
    $numbers = @(([regex]::Match($Version, '^\d+(\.\d+){0,3}').Value -split '\.') | Where-Object { $_ -ne "" })
    while ($numbers.Count -lt 4) { $numbers += "0" }
    $header = @(
        "#define ROWBOT_VERSION_COMMA $($numbers[0]),$($numbers[1]),$($numbers[2]),$($numbers[3])",
        "#define ROWBOT_VERSION_STR `"$Version`""
    )
    Set-Content -Path (Join-Path $OutDir "row_bot_launcher_version.h") -Value $header -Encoding ascii

    $sourceDir = Join-Path $PSScriptRoot "windows"
    $source = Join-Path $sourceDir "row_bot_launcher.c"
    $resourceScript = Join-Path $sourceDir "row_bot_launcher.rc"
    $resource = Join-Path $OutDir "row_bot_launcher.res"
    $object = Join-Path $OutDir "row_bot_launcher.obj"
    $exe = Join-Path $OutDir "Row-Bot.exe"
    $serverSource = Join-Path $sourceDir "row_bot_server.c"
    $serverResourceScript = Join-Path $sourceDir "row_bot_server.rc"
    $serverResource = Join-Path $OutDir "row_bot_server.res"
    $serverObject = Join-Path $OutDir "row_bot_server.obj"
    $serverExe = Join-Path $OutDir "Row-Bot Server.exe"
    Remove-Item -LiteralPath $exe, $serverExe -ErrorAction SilentlyContinue
    $commands = @(
        "`"$vcvars`" >nul",
        "rc /nologo /I `"$OutDir`" /I `"$sourceDir`" /I `"$ProjectRoot`" /fo `"$resource`" `"$resourceScript`"",
        "cl /nologo /utf-8 /W4 /WX /O2 /MT /GS /guard:cf /DNDEBUG /Fo`"$object`" /Fe`"$exe`" `"$source`" `"$resource`" /link /SUBSYSTEM:WINDOWS /MANIFEST:NO /DYNAMICBASE /NXCOMPAT /GUARD:CF /CETCOMPAT",
        "rc /nologo /I `"$OutDir`" /I `"$sourceDir`" /I `"$ProjectRoot`" /fo `"$serverResource`" `"$serverResourceScript`"",
        "cl /nologo /utf-8 /W4 /WX /O2 /MT /GS /guard:cf /DNDEBUG /Fo`"$serverObject`" /Fe`"$serverExe`" `"$serverSource`" `"$serverResource`" /link /SUBSYSTEM:CONSOLE /MANIFEST:NO /DYNAMICBASE /NXCOMPAT /GUARD:CF /CETCOMPAT"
    ) -join " && "
    & cmd.exe /d /c $commands
    if ($LASTEXITCODE -ne 0 -or !(Test-Path $exe) -or !(Test-Path $serverExe)) {
        throw "Building Row-Bot.exe and Row-Bot Server.exe failed."
    }
    Write-Host "      Built $exe and $serverExe" -ForegroundColor Green
}

# â”€â”€ Create build directory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if (!(Test-Path $BuildDir)) {
    New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
}

# Built first: a compiler error fails the build before the long package install.
Write-Host "Building Row-Bot.exe..." -ForegroundColor Yellow
Build-RowBotLauncher -Version $RowBotVersion -OutDir $BuildDir

if (!$SkipDownloads) {
    # â”€â”€ 1. Download Python Embeddable Package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $PythonZip = "python-$PythonVersion-embed-amd64.zip"
    $PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonZip"
    $PythonZipPath = Join-Path $BuildDir $PythonZip
    $PythonDir = Join-Path $BuildDir "python"

    if (!(Test-Path $PythonZipPath)) {
        Write-Host "[1/2] Downloading Python $PythonVersion embeddable package..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZipPath -UseBasicParsing
        Write-Host "      Downloaded: $PythonZip" -ForegroundColor Green
    } else {
        Write-Host "[1/2] Python zip already exists, skipping download." -ForegroundColor DarkGray
    }

    # Extract Python
    if (Test-Path $PythonDir) {
        Remove-Item -Recurse -Force $PythonDir
    }
    Write-Host "      Extracting Python..." -ForegroundColor Yellow
    Expand-Archive -Path $PythonZipPath -DestinationPath $PythonDir -Force
    Write-Host "      Extracted to: $PythonDir" -ForegroundColor Green

    # â”€â”€ 2. Download get-pip.py â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $GetPipPath = Join-Path $BuildDir "get-pip.py"
    if (!(Test-Path $GetPipPath)) {
        Write-Host "[2/2] Downloading get-pip.py..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPipPath -UseBasicParsing
        Write-Host "      Downloaded: get-pip.py" -ForegroundColor Green
    } else {
        Write-Host "[2/2] get-pip.py already exists, skipping download." -ForegroundColor DarkGray
    }
} else {
    Write-Host "Skipping downloads (using existing build/ contents)." -ForegroundColor DarkGray
}

# â”€â”€ Bundle tkinter into embedded Python (not included in embeddable zip) â”€â”€â”€â”€â”€
Write-Host ""
Write-Host "Bundling tkinter into embedded Python..." -ForegroundColor Yellow

# Locate the exact system Python that has tkinter. Native extension modules
# such as _tkinter.pyd must match the embedded Python patch version.
$SysPyInfoJson = & python -c "import json, sys; print(json.dumps({'base_prefix': sys.base_prefix, 'version': '.'.join(map(str, sys.version_info[:3])), 'executable': sys.executable}))" 2>$null
if ($LASTEXITCODE -ne 0 -or !$SysPyInfoJson) {
    Write-Host "ERROR: Could not inspect system Python for tkinter bundling." -ForegroundColor Red
    exit 1
}
$SysPyInfo = $SysPyInfoJson | ConvertFrom-Json
$SysPyRoot = [string]$SysPyInfo.base_prefix
$SysPyVersion = [string]$SysPyInfo.version
$SysPyExe = [string]$SysPyInfo.executable
Write-Host "      Embedded Python target: $PythonVersion" -ForegroundColor DarkGray
Write-Host "      Tk source Python: $SysPyVersion at $SysPyExe" -ForegroundColor DarkGray
Write-Host "      Tk source root: $SysPyRoot" -ForegroundColor DarkGray

if ($SysPyVersion -ne $PythonVersion) {
    Write-Host "ERROR: Tk source Python $SysPyVersion does not match embedded Python $PythonVersion." -ForegroundColor Red
    Write-Host "       Use the exact same patch version for actions/setup-python and -PythonVersion." -ForegroundColor Red
    exit 1
}

function Resolve-TkSourceFile {
    param([string]$FileName)
    $candidates = @(
        (Join-Path "$SysPyRoot\DLLs" $FileName),
        (Join-Path $SysPyRoot $FileName)
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

$RequiredTkPaths = @(
    (Join-Path "$SysPyRoot\Lib" "tkinter"),
    (Join-Path "$SysPyRoot\tcl" "tcl8.6"),
    (Join-Path "$SysPyRoot\tcl" "tk8.6"),
    (Resolve-TkSourceFile "_tkinter.pyd"),
    (Resolve-TkSourceFile "tcl86t.dll"),
    (Resolve-TkSourceFile "tk86t.dll"),
    (Resolve-TkSourceFile "zlib1.dll")
)
$MissingTkPaths = @()
foreach ($requiredTkPath in $RequiredTkPaths) {
    if ([string]::IsNullOrWhiteSpace([string]$requiredTkPath) -or !(Test-Path $requiredTkPath)) {
        $MissingTkPaths += [string]$requiredTkPath
    }
}
if ($MissingTkPaths.Count -gt 0) {
    Write-Host "ERROR: System Python is missing required tkinter/Tcl/Tk files:" -ForegroundColor Red
    foreach ($missing in $MissingTkPaths) {
        Write-Host "       $missing" -ForegroundColor Red
    }
    exit 1
} else {
    $PythonDir = Join-Path $BuildDir "python"

    # Copy _tkinter.pyd and Tcl/Tk DLLs
    foreach ($dll in @("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")) {
        $src = Resolve-TkSourceFile $dll
        if (Test-Path $src) {
            Copy-Item $src -Destination $PythonDir -Force
            Write-Host "      Copied $dll from $src" -ForegroundColor Green
        } else {
            Write-Host "ERROR: $dll not found in $SysPyRoot." -ForegroundColor Red
            exit 1
        }
    }

    # Copy tkinter Python package
    $TkPkgSrc = Join-Path "$SysPyRoot\Lib" "tkinter"
    $TkPkgDst = Join-Path $PythonDir "Lib\tkinter"
    if (Test-Path $TkPkgSrc) {
        if (Test-Path $TkPkgDst) { Remove-Item -Recurse -Force $TkPkgDst }
        Copy-Item $TkPkgSrc -Destination $TkPkgDst -Recurse -Force
        Write-Host "      Copied Lib\tkinter\" -ForegroundColor Green
    }

    # Copy Tcl/Tk runtime data (tcl8.6, tk8.6, tcl8)
    $TclDst = Join-Path $PythonDir "tcl"
    if (!(Test-Path $TclDst)) { New-Item -ItemType Directory -Path $TclDst -Force | Out-Null }
    foreach ($subdir in @("tcl8.6", "tk8.6", "tcl8")) {
        $src = Join-Path "$SysPyRoot\tcl" $subdir
        $dst = Join-Path $TclDst $subdir
        if (Test-Path $src) {
            if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
            Copy-Item $src -Destination $dst -Recurse -Force
            Write-Host "      Copied tcl\$subdir\" -ForegroundColor Green
        }
    }

    Write-Host "      tkinter bundling complete." -ForegroundColor Green
}

# â”€â”€ Pre-install Python packages (self-contained installer) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Write-Host ""
Write-Host "Pre-installing Python packages into embedded Python..." -ForegroundColor Yellow

$PythonDir = Join-Path $BuildDir "python"
$PythonExe = Join-Path $PythonDir "python.exe"

# Prevent embedded Python from picking up system-wide packages
$env:PYTHONNOUSERSITE = "1"

# Patch ._pth files to enable pip and site-packages
Write-Host "      Patching ._pth files..." -ForegroundColor Yellow
Get-ChildItem "$PythonDir\python*._pth" | ForEach-Object {
    $content = Get-Content $_.FullName
    $content = $content -replace '^#import site', 'import site'

    $lines = @($content)
    if ($lines -notcontains 'Lib\site-packages') { $lines += 'Lib\site-packages' }
    if ($lines -notcontains '..\app')             { $lines += '..\app' }
    if ($lines -notcontains '..\app\src')         { $lines += '..\app\src' }
    if ($lines -notcontains 'Lib')                { $lines += 'Lib' }

    $lines | Set-Content $_.FullName
    Write-Host "      Patched $($_.Name)" -ForegroundColor Green
}

# Verify embedded tkinter before any artifact is produced. A failed import here
# means the packaged splash and first-run chooser would fail on user machines.
Write-Host "      Verifying embedded tkinter..." -ForegroundColor Yellow
$env:TCL_LIBRARY = Join-Path $PythonDir "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $PythonDir "tcl\tk8.6"
$env:PATH = (Join-Path $PythonDir "Scripts") + ";" + $PythonDir + ";" + $env:PATH
$TkSmokeCode = @"
import os
import sys
py_dir = os.path.dirname(sys.executable)
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(py_dir)
    dll_dir = os.path.join(py_dir, "DLLs")
    if os.path.isdir(dll_dir):
        os.add_dll_directory(dll_dir)
import _tkinter
import tkinter
interp = tkinter.Tcl()
print("tcl_patchlevel=" + interp.eval("info patchlevel"))
try:
    root = tkinter.Tk()
    root.withdraw()
    root.update()
    root.destroy()
    print("tk_root=ok")
except Exception as exc:
    print("tk_root=skipped:" + repr(exc))
"@
& $PythonExe -c $TkSmokeCode
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Embedded Python could not import tkinter/_tkinter." -ForegroundColor Red
    exit 1
}
Write-Host "      Embedded tkinter verified" -ForegroundColor Green

# Install pip
Write-Host "      Installing pip..." -ForegroundColor Yellow
$GetPipPath = Join-Path $BuildDir "get-pip.py"
& $PythonExe $GetPipPath --no-warn-script-location 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install pip." -ForegroundColor Red
    exit 1
}

# Add Scripts to PATH so pip-installed commands are found
$env:PATH = (Join-Path $PythonDir "Scripts") + ";" + $env:PATH
Write-Host "      pip installed" -ForegroundColor Green

# Install build tools
Write-Host "      Installing setuptools and wheel..." -ForegroundColor Yellow
& $PythonExe -m pip install --no-warn-script-location setuptools wheel --quiet 2>&1 | Out-Null

# Install all packages from the locked requirements export.
$RequirementsFile = Join-Path (Split-Path $PSScriptRoot) "requirements.txt"
$LockFile = Join-Path (Split-Path $PSScriptRoot) "uv.lock"
if (!(Test-Path $LockFile)) {
    Write-Host "ERROR: uv.lock not found. Regenerate dependencies before building." -ForegroundColor Red
    exit 1
}
Write-Host "      Installing locked Python packages from requirements.txt..." -ForegroundColor Yellow
Write-Host "      (this may take several minutes)" -ForegroundColor Yellow
& $PythonExe -m pip install --no-warn-script-location --no-compile --no-deps -r $RequirementsFile 2>&1 | ForEach-Object {
    $line = $_.ToString()
    if ($line -match 'Successfully installed|Installing collected') {
        Write-Host "      $line" -ForegroundColor Green
    } elseif ($line -match 'ERROR:|error:|Could not|Failed') {
        Write-Host "      $line" -ForegroundColor Red
    }
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install Python packages." -ForegroundColor Red
    exit 1
}
Write-Host "      All packages pre-installed" -ForegroundColor Green

# Verify critical runtime imports before producing an installer. This catches
# resolver/build issues where pip finishes but required packages are not
# importable in the embedded Python.
$VerifierFile = Join-Path (Split-Path $PSScriptRoot) "scripts\verify_runtime_dependencies.py"
Write-Host "      Verifying required runtime packages..." -ForegroundColor Yellow
& $PythonExe $VerifierFile all
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Embedded Python is missing required runtime packages." -ForegroundColor Red
    exit 1
}
Write-Host "      Required runtime packages verified" -ForegroundColor Green

# Stage the app payload (scripts/app_payload_manifest.py) into a fresh folder,
# with only the validated client build's inventory.
$AppStage = Join-Path $BuildDir "app"
if (Test-Path $AppStage) { Remove-Item -LiteralPath $AppStage -Recurse -Force }
& $PythonExe (Join-Path $ProjectRoot "scripts\app_payload_manifest.py") --project-root $ProjectRoot --stage $AppStage
if ($LASTEXITCODE -ne 0) { throw "App payload staging failed." }
$ClientVerifier = Join-Path $ProjectRoot "scripts\verify_client_assets.py"
& $PythonExe $ClientVerifier --root $ClientBuild
if ($LASTEXITCODE -ne 0) { throw "Client build validation failed." }
$ClientAssetStage = Join-Path $AppStage "src\row_bot\static\client-v2"
& $ClientNode (Join-Path $ProjectRoot "frontend\scripts\asset-manifest.mjs") $ClientBuild --package-dir $ClientAssetStage
if ($LASTEXITCODE -ne 0) { throw "Client asset staging failed." }
& $PythonExe $ClientVerifier --root $ClientAssetStage --compare $ClientBuild --strict
if ($LASTEXITCODE -ne 0) { throw "Staged client asset validation failed." }

# Playwright Chromium is not bundled (about 735 MB installed), as on macOS and
# Linux: Row-Bot.exe points Playwright at <data dir>\playwright-browsers and the
# user's Browser Automation Install action downloads the matching Chromium into
# the data folder. A local build can still bundle it.
$BundledBrowsers = Join-Path $PythonDir "playwright-browsers"
if ($BundlePlaywright -or $env:BUNDLE_PLAYWRIGHT -eq "1") {
    Write-Host "      Bundling Playwright Chromium (local build)..." -ForegroundColor Yellow
    $env:PLAYWRIGHT_BROWSERS_PATH = $BundledBrowsers
    & $PythonExe -m playwright install chromium 2>&1 | ForEach-Object {
        if ($_ -match 'downloading|Chromium') {
            Write-Host "      $_" -ForegroundColor Green
        }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Playwright Chromium install failed. Browser Automation needs the user's Install or Repair." -ForegroundColor DarkYellow
    }
    Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH
} else {
    # A folder left by an earlier local build (-SkipDownloads keeps python\) must not ship.
    if (Test-Path -LiteralPath $BundledBrowsers) { Remove-Item -LiteralPath $BundledBrowsers -Recurse -Force }
    Write-Host "      Playwright Chromium is not bundled; Browser Automation's Install action downloads it" -ForegroundColor DarkGray
}

# -- Trim the runtime and compile its bytecode --
# The rules are shared with the macOS and Linux builds: src\row_bot\payload_trim.json.
$PayloadTrim = Join-Path $ProjectRoot "src\row_bot\payload_trim.py"
Write-Host ""
Write-Host "Trimming the runtime (test suites, headers, unused Google API documents, launchers)..." -ForegroundColor Yellow
& $PythonExe -I -S $PayloadTrim trim --python-root $PythonDir
if ($LASTEXITCODE -ne 0) { throw "Trimming the runtime failed." }

# Version folders are shared through hard links by later updates, so they
# never change after install: ship .pyc that need no rewrite (unchecked-hash)
# and that do not depend on the build folder (-s), and run with
# PYTHONDONTWRITEBYTECODE=1 (Row-Bot.exe sets it). The app gets bytecode, and so
# does every runtime module the runtime check imports (it runs again here, on
# the trimmed runtime).
Write-Host "Compiling deterministic bytecode..." -ForegroundColor Yellow
& $PythonExe -I -S $PayloadTrim bytecode --python-root $PythonDir --app-root $AppStage --version-root $BuildDir
if ($LASTEXITCODE -ne 0) { throw "Compiling bytecode failed." }
Write-Host "      Bytecode compiled" -ForegroundColor Green

# version.json describes this version folder (src/row_bot/install_layout.py).
& $PythonExe -I -S (Join-Path $ProjectRoot "src\row_bot\install_layout.py") version-json `
    --version $RowBotVersion --python $PythonVersion --lock $LockFile --out (Join-Path $BuildDir "version.json")
if ($LASTEXITCODE -ne 0) { throw "Writing version.json failed." }

# â”€â”€ 3. Create dist directory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$DistDir = Join-Path (Join-Path $PSScriptRoot "..") "dist"
if (!(Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# â”€â”€ 4. Compile with Inno Setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Write-Host ""
Write-Host "Compiling installer with Inno Setup..." -ForegroundColor Yellow

$IssFile = Join-Path $PSScriptRoot "row_bot_setup.iss"

# Try to find ISCC.exe
[string[]]$IsccPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source
) | Where-Object { $_ -and (Test-Path $_) }

if ($IsccPaths.Count -eq 0) {
    Write-Host ""
    Write-Host "ERROR: Inno Setup (ISCC.exe) not found!" -ForegroundColor Red
    Write-Host "Download from: https://jrsoftware.org/isdl.php" -ForegroundColor Red
    Write-Host ""
    Write-Host "Build directory is ready at: $BuildDir" -ForegroundColor Yellow
    Write-Host "After installing Inno Setup, run:" -ForegroundColor Yellow
    Write-Host "  iscc /DAppStageDir=`"$AppStage`" `"$IssFile`"" -ForegroundColor White
    exit 1
}

$Iscc = $IsccPaths[0]
Write-Host "Using ISCC: $Iscc" -ForegroundColor DarkGray

& $Iscc "/DAppStageDir=$AppStage" $IssFile

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host " Installer built successfully!"               -ForegroundColor Green
    Write-Host " Output: dist\Row-Bot-$RowBotVersion-Windows-x64.exe" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "ERROR: Inno Setup compilation failed." -ForegroundColor Red
    exit 1
}
