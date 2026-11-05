<#
.SYNOPSIS
    Sign, pack and publish a Row-Bot release on the Windows signing machine (D4).

.DESCRIPTION
    release.yml builds every release file in CI and leaves a draft GitHub release
    with all of them except the Windows installer: Row-Bot.exe must be signed
    before Inno Setup packs it, and the Certum certificate never leaves this
    machine. This script, with one SimplySign login:

      1. finds the release run and checks it built the tag's current commit,
      2. downloads the Windows installer stage that run built (python\, the app,
         version.json, Row-Bot.exe and the Inno Setup script), or reuses one it
         unpacked and checked for the same run and commit,
      3. signs Row-Bot.exe (signtool /tr /td sha256, Certum through SimplySign),
         runs ISCC on the stage with the Inno Setup version CI used, signs the
         installer and verifies both signatures,
      4. checks the release: every file on the draft is one this run built (CI's
         checksum, or notarize-finish's record for the DMG) and nothing else is
         there; every installer and update part is present and agrees with the
         update description; macOS is stapled; the notes say when the full
         installer is needed (D15, D16),
      5. uploads the installer to the draft and writes the SHA-256 block over all
         final files,
      6. after you type the confirmation, checks that the draft still holds
         exactly those files, publishes it by its release id, and checks again.

    It refuses to publish while macOS is not stapled or a file is missing or
    foreign. Every step can be run again: signed files and a built installer
    are kept. The decisions are made by scripts/release_publish.py and
    scripts/windows_stage.py, which are tested.

    -DryRun does everything except signing, uploading, editing the release and
    publishing; it reads the other files from the run's artifacts. release.yml
    runs it on Windows for every dry run.

    -Resign re-signs the Windows installer of a published release, when the
    certificate that signed it was revoked: the same run's stage is signed with
    the new certificate and packed again, and after you type the confirmation
    only the installer and its line in the SHA-256 block are replaced. Every
    other file must still be the one the block lists. The update description
    names the installer without a hash, and the update parts hold no programs.

    Before the first signature the certificate is checked with a fresh OCSP
    answer (certutil -verify -urlfetch): Windows keeps a CRL for a week, so its
    own checks can call a revoked certificate valid.

    Prerequisites: Inno Setup 6 (ISCC.exe, the version CI used), the Windows
    SDK's signtool.exe, SimplySign Desktop logged in with the Certum code signing
    certificate, the GitHub CLI (gh auth login) with write access, and Python
    3.11 or newer.

.EXAMPLE
    .\scripts\release_windows.ps1 -Version 5.2.0-beta.1

.EXAMPLE
    .\scripts\release_windows.ps1 -Version 5.2.0-beta.1 -PauseBeforePublish

.EXAMPLE
    .\scripts\release_windows.ps1 -Version 5.2.0-beta.1 -RunId 38200000000 -DryRun

.EXAMPLE
    .\scripts\release_windows.ps1 -Version 5.3.2 -Resign
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    # The release.yml run that built the release (default: its newest successful run for the tag).
    [string]$RunId,
    [switch]$DryRun,
    [string]$WorkDir,
    [string]$Repository,
    # The Certum code signing certificate (its thumbprint is public).
    [string]$CertificateSha1 = "2341B4B36A21DF948E538A88BB194FAE4D1CAE51",
    [string]$TimestampUrl = "http://time.certum.pl",
    [string]$SignTool,
    [string]$Iscc,
    # Pack with a different Inno Setup than CI smoke-tested (not recommended).
    [switch]$AllowInnoMismatch,
    # Stop before the confirmation so the signed installer on the draft can be installed and tried first.
    [switch]$PauseBeforePublish,
    # Re-sign the Windows installer of a published release (its certificate was revoked).
    [switch]$Resign
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot
$Tag = "v$Version"
$Utf8 = New-Object System.Text.UTF8Encoding $false
# gh and Python print UTF-8; Windows PowerShell 5.1 otherwise reads them in the console's code page and
# writes the garbled release notes back to the draft ("Settings ΓÇ║ Updates").
[Console]::OutputEncoding = $Utf8

function Write-Step([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Stop-Release([string]$Text) { throw "Refusing to continue: $Text" }

# Native programs: their error output is never redirected (Windows PowerShell 5.1
# turns redirected error output into terminating errors); the exit code decides.
function Invoke-Native {
    # Runs a program with its output on the console; fails on a non-zero exit code.
    param([Parameter(Mandatory = $true)][string]$File, [string[]]$Arguments = @())
    & $File @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($File)) failed with exit code $LASTEXITCODE" }
}

function Get-NativeOutput {
    # Runs a program and returns its standard output; fails on a non-zero exit code.
    param([Parameter(Mandatory = $true)][string]$File, [string[]]$Arguments = @())
    $output = & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($File)) failed with exit code $LASTEXITCODE" }
    return $output
}

function Find-Tool([string]$Given, [string[]]$Candidates, [string]$Name) {
    if ($Given) { if (Test-Path $Given) { return (Resolve-Path $Given).Path }; throw "$Name not found at $Given" }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) { return (Resolve-Path $candidate).Path }
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Find-SignTool {
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    $found = @()
    if (Test-Path $kits) {
        $found = @(Get-ChildItem -Path $kits -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+\.' } | Sort-Object { [version]$_.Name } -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" })
    }
    return Find-Tool $SignTool $found "signtool.exe"
}

function Find-Python {
    foreach ($candidate in @(@("python"), @("py", "-3"))) {
        $command = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $prefix = @($candidate | Select-Object -Skip 1)
        $versionText = & $command.Source @prefix -c "import sys; print('%d.%d' % sys.version_info[:2])"
        if ($LASTEXITCODE -eq 0 -and $versionText -and ([version]$versionText -ge [version]"3.11")) {
            return @{ File = $command.Source; Prefix = $prefix }
        }
    }
    throw "Python 3.11 or newer is required (python or py -3 on PATH)"
}

function Invoke-Python([string[]]$Arguments) {
    return Get-NativeOutput -File $Python.File -Arguments (@($Python.Prefix) + @("-X", "utf8") + $Arguments)
}

function Test-Python([string[]]$Arguments) {
    # Runs a decision of scripts/release_publish.py: its messages on the console, $true when it passes.
    & $Python.File @($Python.Prefix) -X utf8 @Arguments | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Get-Sha256([string]$Path) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }

function Save-Text([string]$Path, [string]$Text) { [IO.File]::WriteAllText($Path, $Text, $Utf8) }

function Save-Artifact([string]$Name, [string]$Into, [switch]$Optional) {
    # One artifact of the release run, into its own folder ($null if optional and missing).
    if (Test-Path $Into) { Remove-Item -LiteralPath $Into -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Into | Out-Null
    if ($Optional) {
        $names = Get-NativeOutput -File $Gh -Arguments @("api", "repos/$Repository/actions/runs/$RunId/artifacts?per_page=100",
                 "--jq", ".artifacts[] | select(.expired | not) | .name")
        if (-not (@($names) -contains $Name)) { return $null }
    }
    Invoke-Native -File $Gh -Arguments @("run", "download", $RunId, "-R", $Repository, "-n", $Name, "-D", $Into)
    return $Into
}

function Get-Draft {
    # The one draft release of the tag, by id; refuses none, several, or a published one.
    # No quotes inside the jq: Windows PowerShell 5.1 drops them when it calls gh, so the tag is matched here.
    $rows = @(Get-NativeOutput -File $Gh -Arguments @("api", "repos/$Repository/releases?per_page=100", "--paginate",
              "--jq", ".[] | [.id, .tag_name] | @tsv"))
    $ids = @($rows | Where-Object { $_ } | ForEach-Object { $id, $name = $_ -split "`t", 2; if ($name -eq $Tag) { $id } })
    if ($ids.Count -eq 0) { Stop-Release "there is no release $Tag (release.yml creates the draft)" }
    if ($ids.Count -gt 1) { Stop-Release "$($ids.Count) releases are tagged $Tag; delete the extra drafts" }
    if ($script:ReleaseId -and $ids[0] -ne $script:ReleaseId) { Stop-Release "the release of $Tag changed (id $($ids[0]))" }
    $script:ReleaseId = [string]$ids[0]
    return Get-Release
}

function Get-Release {
    # The release by id, saved to a file for scripts/release_publish.py; returns it parsed.
    $json = (Get-NativeOutput -File $Gh -Arguments @("api", "repos/$Repository/releases/$script:ReleaseId")) -join "`n"
    Save-Text $ReleaseFile $json
    return $json | ConvertFrom-Json
}

function Save-DraftAssets([string]$Out) {
    # The draft's files with their SHA-256 (GitHub's digest, or downloaded and hashed).
    $downloads = Join-Path $WorkDir "downloads"
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    $missing = @(Invoke-Python @((Join-Path $ScriptRoot "release_publish.py"), "missing-digests", "--release", $ReleaseFile) |
                 Where-Object { $_ })
    foreach ($name in $missing) {
        Invoke-Native -File $Gh -Arguments @("release", "download", $Tag, "-R", $Repository, "-p", $name, "-D", $downloads, "--clobber")
    }
    Invoke-Python @((Join-Path $ScriptRoot "release_publish.py"), "draft-assets", "--release", $ReleaseFile,
        "--downloads", $downloads, "--out", $Out) | Out-Null
}

function Test-OurSignature([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    return ($signature.Status -eq "Valid" -and $signature.SignerCertificate -and
            $signature.SignerCertificate.Thumbprint -eq $CertificateSha1.ToUpperInvariant())
}

function Assert-NotRevoked($Certificate) {
    # certutil fetches a fresh OCSP answer; its exit code is 0 even for a revoked certificate.
    $cer = Join-Path $scratch "signing.cer"
    $answer = Join-Path $scratch "certutil.txt"
    [IO.File]::WriteAllBytes($cer, $Certificate.RawData)
    Save-Text $answer ((& certutil.exe -verify -urlfetch $cer) -join "`n")
    if (-not (Test-Python @($Publish, "check-revocation", "--certutil-output", $answer))) {
        Stop-Release "the certificate $CertificateSha1 cannot sign"
    }
    Write-Note "Certificate $CertificateSha1 is not revoked (OCSP); it expires $($Certificate.NotAfter.ToString('yyyy-MM-dd'))"
}

function Assert-SigningReady {
    # SimplySign Desktop exposes the Certum certificate while you are logged in:
    # log in once, before the first signature, and both files are signed in that session.
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $certificate = Get-ChildItem -Path "Cert:\CurrentUser\My\$CertificateSha1" -ErrorAction SilentlyContinue
        if ($certificate -and $certificate.HasPrivateKey) { Assert-NotRevoked $certificate; return }
        Write-Host "The Certum certificate $CertificateSha1 is not available." -ForegroundColor Yellow
        Read-Host "Log in to SimplySign Desktop (the code from your phone), then press Enter" | Out-Null
    }
    throw "The Certum certificate is still not available; is SimplySign Desktop logged in?"
}

function Invoke-Sign([string]$Path) {
    if (Test-OurSignature $Path) { Write-Note "$([IO.Path]::GetFileName($Path)) is already signed"; return }
    Invoke-Native -File $SignToolPath -Arguments @(
        "sign", "/sha1", $CertificateSha1, "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256",
        "/d", "Row-Bot", "/du", "https://row-bot.ai", $Path)
}

function Assert-Signed([string]$Path) {
    Get-NativeOutput -File $SignToolPath -Arguments @("verify", "/pa", "/v", $Path) | Out-Null
    if (-not (Test-OurSignature $Path)) { throw "$Path is not signed with $CertificateSha1" }
    Write-Note "$([IO.Path]::GetFileName($Path)): signature verified"
}

# ── Preflight ───────────────────────────────────────────────────────────────

if ($Version -notmatch '^\d+\.\d+\.\d+(-(alpha|beta|rc)\.\d+)?$') { throw "Version must look like 5.2.0 or 5.2.0-beta.1" }
if ($Resign -and $DryRun) { throw "-Resign signs and replaces a published file; it has no dry run" }
$Gh = Find-Tool $null @() "gh"
if (-not $Gh) { throw "The GitHub CLI (gh) is required: https://cli.github.com, then gh auth login" }
if (-not $Repository) { $Repository = if ($env:GH_REPO) { $env:GH_REPO } else { "siddsachar/row-bot" } }
$Python = Find-Python
$Publish = Join-Path $ScriptRoot "release_publish.py"
$IsccPath = Find-Tool $Iscc @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe") "iscc.exe"
if (-not $IsccPath) { throw "Inno Setup 6 (ISCC.exe) is required: https://jrsoftware.org/isdl.php" }
$SignToolPath = $null
if (-not $DryRun) {
    $SignToolPath = Find-SignTool
    if (-not $SignToolPath) { throw "signtool.exe is required (Windows SDK, Signing Tools for Desktop Apps)" }
}
$script:ReleaseId = $null

Write-Step "Row-Bot $Version$(if ($DryRun) { ' (dry run: no signing, no upload, no publishing)' })"
Write-Note "Repository: $Repository"

# ── 1. The release run ──────────────────────────────────────────────────────

Write-Step "1. The release run"
$scratch = Join-Path ([IO.Path]::GetTempPath()) "row-bot-release-$PID"
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
if (-not $RunId) {
    $runsFile = Join-Path $scratch "runs.json"
    Save-Text $runsFile ((Get-NativeOutput -File $Gh -Arguments @("run", "list", "-R", $Repository, "--workflow", "release.yml",
        "--status", "success", "--limit", "100", "--json", "databaseId,displayTitle,headBranch,conclusion")) -join "`n")
    $pick = @("select-run", "--runs", $runsFile, "--version", $Version)
    if ($DryRun) { $pick += "--dry-run" }
    $RunId = (Invoke-Python (@($Publish) + $pick) | Select-Object -Last 1).Trim()
}
$runFile = Join-Path $scratch "run.json"
Save-Text $runFile ((Get-NativeOutput -File $Gh -Arguments @("run", "view", $RunId, "-R", $Repository, "--json",
    "databaseId,displayTitle,headBranch,headSha,status,conclusion,url")) -join "`n")
$run = Get-Content $runFile -Raw | ConvertFrom-Json
Write-Note "$($run.displayTitle) - $($run.url)"
$checkRun = @($Publish, "check-run", "--run", $runFile, "--version", $Version)
if ($DryRun) {
    $checkRun += "--dry-run"
} else {
    # The tag's commit now: a run of an earlier commit (the tag moved since) is never published.
    $tagSha = (Get-NativeOutput -File $Gh -Arguments @("api", "repos/$Repository/commits/$Tag", "--jq", ".sha") | Select-Object -Last 1).Trim()
    $checkRun += @("--tag-sha", $tagSha)
}
if (-not (Test-Python $checkRun)) { Stop-Release "run $RunId cannot be published as $Version" }

# One work folder per run: nothing of another run (or a dry run) is ever reused.
if (-not $WorkDir) {
    # Short: the stage's deepest paths are as long as an installed version's.
    $base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { Join-Path $env:LOCALAPPDATA "rbr" }
    $WorkDir = Join-Path $base $RunId
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$WorkDir = (Resolve-Path $WorkDir).Path
$ReleaseFile = Join-Path $WorkDir "release.json"
Write-Note "Work folder: $WorkDir"

# ── 2. The installer stage ──────────────────────────────────────────────────

Write-Step "2. The Windows installer stage"
$Stage = Join-Path $WorkDir "s"
$IssFile = Join-Path $Stage "installer\row_bot_setup.iss"
$stageArgs = @("--run-id", $RunId, "--sha", $run.headSha)
if (-not (Test-Python (@((Join-Path $ScriptRoot "windows_stage.py"), "check", "--stage", $Stage) + $stageArgs))) {
    $download = Save-Artifact "Row-Bot-Windows-stage" (Join-Path $WorkDir "download")
    $tarFile = Get-ChildItem -Path $download -Filter "*.tar" | Select-Object -First 1
    if (-not $tarFile) { throw "The Row-Bot-Windows-stage artifact holds no .tar" }
    # Unpacked into an empty folder, refused if another run or commit built it, counted.
    Invoke-Python (@((Join-Path $ScriptRoot "windows_stage.py"), "unpack", "--archive", $tarFile.FullName, "--out", $Stage) + $stageArgs) |
        ForEach-Object { Write-Note "Stage: $_" }
    Remove-Item -LiteralPath $download -Recurse -Force
}
$stageVersion = (Get-Content (Join-Path $Stage "installer\build\version.json") -Raw | ConvertFrom-Json).version
$issVersion = (Select-String -Path $IssFile -Pattern '#define\s+MyAppVersion\s+"([^"]+)"').Matches[0].Groups[1].Value
if ($stageVersion -ne $Version -or $issVersion -ne $Version) { Stop-Release "the stage is $stageVersion (setup script $issVersion), not $Version" }
$ciInno = (Get-Content (Join-Path $Stage "installer\build\inno-version.txt") -Raw).Trim()
$localInno = (Get-Item $IsccPath).VersionInfo.ProductVersion
if ($ciInno -ne $localInno) {
    if (-not $AllowInnoMismatch) {
        Stop-Release "Inno Setup here is $localInno, but CI smoke-tested an installer packed by $ciInno. Install $ciInno, or pass -AllowInnoMismatch"
    }
    Write-Host "   Packing with Inno Setup $localInno; CI smoke-tested $ciInno (-AllowInnoMismatch)" -ForegroundColor Yellow
}
$Launcher = Join-Path $Stage "installer\build\Row-Bot.exe"
$Server = Join-Path $Stage "installer\build\Row-Bot Server.exe"
Write-Note "Stage of $Version from run $RunId ($($run.headSha)) ready"

# ── 3. Sign Row-Bot.exe, pack, sign the installer ───────────────────────────

$Installer = Join-Path $Stage "dist\Row-Bot-$Version-Windows-x64.exe"
$InstallerName = [IO.Path]::GetFileName($Installer)
if ($DryRun) {
    Write-Step "3. Sign, pack, sign: Row-Bot.exe and the installer are not signed (dry run)"
} else {
    Write-Step "3. Sign Row-Bot.exe and Row-Bot Server.exe, pack the installer, sign it"
    Assert-SigningReady
    Invoke-Sign $Launcher
    Assert-Signed $Launcher
    Invoke-Sign $Server
    Assert-Signed $Server
}
$packed = (Test-Path $Installer) -and ((Get-Item $Installer).LastWriteTimeUtc -gt (Get-Item $Launcher).LastWriteTimeUtc)
if ($packed) {
    Write-Note "$InstallerName is newer than Row-Bot.exe; kept"
} else {
    Invoke-Native -File $IsccPath -Arguments @("/Qp", "/DAppStageDir=$(Join-Path $Stage 'installer\build\app')", $IssFile)
}
if (-not (Test-Path $Installer)) { throw "ISCC did not write $Installer" }
if (-not $DryRun) {
    Invoke-Sign $Installer
    Assert-Signed $Launcher
    Assert-Signed $Server
    Assert-Signed $Installer
}

# ── -Resign: replace the installer of the published release ─────────────────

if ($Resign) {
    Write-Step "4. Check the published $Tag"
    $AssetsFile = Join-Path $WorkDir "assets.json"
    $ResignListing = Join-Path $WorkDir "resign-listing.json"
    $resignCheck = @($Publish, "resign-listing", "--release", $ReleaseFile, "--installer-file", $Installer)
    $release = Get-Draft
    Save-DraftAssets $AssetsFile
    if (-not (Test-Python ($resignCheck + @("--assets", $AssetsFile, "--out", $ResignListing)))) {
        Stop-Release "$Tag changed since it was published"
    }
    $installerHash = Get-Sha256 $Installer
    Write-Note "Every other file is still the one its SHA-256 block lists."

    Write-Step "5. Replace $InstallerName on $Tag"
    Write-Note "Re-signed installer: sha256 $installerHash"
    Write-Host "   This replaces the installer of a published release and its line in the SHA-256 block." -ForegroundColor Yellow
    $answer = Read-Host "   Type 'resign $Version' to replace it now (anything else changes nothing)"
    if ($answer -ne "resign $Version") { Write-Note "Nothing changed."; exit 0 }
    # The prompt may have waited a long time: check again right before the upload.
    Get-Draft | Out-Null
    Save-DraftAssets $AssetsFile
    if (-not (Test-Python ($resignCheck + @("--assets", $AssetsFile, "--out", $ResignListing)))) {
        Stop-Release "$Tag changed while waiting"
    }
    Invoke-Native -File $Gh -Arguments @("release", "upload", $Tag, $Installer, "-R", $Repository, "--clobber")
    $release = Get-Release
    $uploaded = @($release.assets | Where-Object { $_.name -eq $InstallerName })
    if ($uploaded.Count -ne 1 -or ($uploaded[0].PSObject.Properties["digest"] -and $uploaded[0].digest -and $uploaded[0].digest -ne "sha256:$installerHash")) {
        throw "The installer on $Tag is not the one signed here; run this script again"
    }
    $BodyFile = Join-Path $WorkDir "body-current.md"
    $NewBody = Join-Path $WorkDir "body-with-manifest.md"
    Save-Text $BodyFile ([string]$release.body)
    Invoke-Python @((Join-Path $ScriptRoot "append_sha_manifest.py"), "--listing", $ResignListing, "--body-file", $BodyFile, "--write-body", $NewBody) | Out-Null
    Invoke-Native -File $Gh -Arguments @("api", "-X", "PATCH", "repos/$Repository/releases/$script:ReleaseId",
        "-f", "tag_name=$Tag", "-F", "body=@$NewBody")
    Get-Release | Out-Null
    $afterFile = Join-Path $WorkDir "assets-resigned.json"
    Save-DraftAssets $afterFile
    if (-not (Test-Python ($resignCheck + @("--assets", $afterFile, "--done")))) {
        throw "Replaced the installer of $Tag, but the release does not hold and list it as signed here; run this script again"
    }
    Write-Step "Re-signed $Tag"
    Write-Note "$InstallerName, sha256 $installerHash, signed with $CertificateSha1"
    exit 0
}

# ── 4. The release's other files and the checks ─────────────────────────────

Write-Step "4. Check the release"
$Files = Join-Path $WorkDir "files"
New-Item -ItemType Directory -Force -Path $Files | Out-Null
$parts = Save-Artifact "Row-Bot-update-parts" (Join-Path $WorkDir "update-parts")
$checksums = Save-Artifact "Row-Bot-release-sha256" (Join-Path $WorkDir "checksums")
$UpdateName = "row-bot-update-$Version.json"
$AssetsFile = Join-Path $WorkDir "assets.json"
$ListingFile = Join-Path $WorkDir "listing.json"
$body = ""
$macosReady = $false
$records = @()

if ($DryRun) {
    foreach ($name in @("Row-Bot-Linux", "Row-Bot-macOS", "Row-Bot-update")) {
        $folder = Save-Artifact $name (Join-Path $WorkDir "artifact-$name") -Optional
        if ($folder) { Get-ChildItem -Path $folder -File | Copy-Item -Destination $Files -Force }
    }
    Get-ChildItem -Path $parts -File | Where-Object { $_.Name -match '^row-bot-(code|assets|pylock)-' } | Copy-Item -Destination $Files -Force
    Invoke-Python @($Publish, "folder-assets", "--dir", $Files, "--out", $AssetsFile) | Out-Null
    $notarization = Save-Artifact "Row-Bot-macOS-notarization" (Join-Path $WorkDir "notarization")
    $readiness = Invoke-Python @("-c", "import json, sys; sys.path.insert(0, sys.argv[2]); import macos_notarize as m; print(m.readiness(json.load(open(sys.argv[1]))))",
        (Join-Path $notarization "notarization.json"), $ScriptRoot)
    $readiness = ($readiness | Select-Object -Last 1).Trim()
    $macosReady = $readiness -in @("ready", "app-only")
    Write-Note "macOS notarization: $readiness$(if ($readiness -eq 'app-only') { ' (a dry run notarizes the app only)' })"
    Invoke-Python @((Join-Path $ScriptRoot "build_update_parts.py"), "release-notes", "--version", $Version,
        "--notes", (Join-Path $ProjectRoot "RELEASE_NOTES.md"), "--wheel-check", (Join-Path $parts "wheel-check.json"),
        "--title-out", (Join-Path $WorkDir "title.txt"), "--body-out", (Join-Path $WorkDir "body.md")) | Out-Null
    $body = [IO.File]::ReadAllText((Join-Path $WorkDir "body.md"))
    $updateJson = Join-Path $Files $UpdateName
} else {
    $release = Get-Draft
    if (-not $release.draft) { Stop-Release "$Tag is already published; a published release is never changed" }
    $body = [string]$release.body
    Save-DraftAssets $AssetsFile
    Invoke-Native -File $Gh -Arguments @("release", "download", $Tag, "-R", $Repository, "-p", $UpdateName, "-D", $Files, "--clobber")
    $updateJson = Join-Path $Files $UpdateName
    # A DMG notarize-finish.yml attached for this run is accepted by its record of it.
    $recordName = "macos-dmg-record-$RunId"
    $recordRun = Get-NativeOutput -File $Gh -Arguments @("api", "repos/$Repository/actions/artifacts?name=$recordName",
        "--jq", "[.artifacts[] | select(.expired | not)][0].workflow_run.id // empty")
    if ($recordRun) {
        $recordDir = Join-Path $WorkDir "record"
        if (Test-Path $recordDir) { Remove-Item -LiteralPath $recordDir -Recurse -Force }
        Invoke-Native -File $Gh -Arguments @("run", "download", "$recordRun".Trim(), "-R", $Repository, "-n", $recordName, "-D", $recordDir)
        $records = @(Get-ChildItem -Path $recordDir -Filter "*.json" | ForEach-Object { $_.FullName })
    }
}
$accept = @($Publish, "accept-assets", "--assets", $AssetsFile, "--version", $Version, "--run-id", $RunId,
            "--installer", $InstallerName, "--installer-file", $Installer,
            "--ci-hashes", (Join-Path $checksums "release-artifact-sha256.txt"), "--out", $ListingFile)
foreach ($record in $records) { $accept += @("--record", $record) }
$problems = @()
if (-not (Test-Python $accept)) { $problems += "the draft holds files this run did not build" }
if (-not $DryRun) {
    $listed = Get-Content $ListingFile -Raw | ConvertFrom-Json
    $macosReady = [bool](@($listed) | Where-Object { $_.name -eq "Row-Bot-$Version-macOS-arm64.dmg" })
    Write-Note "macOS: $(if ($macosReady) { 'the stapled DMG on the draft is this run''s' } else { 'no stapled DMG of this run on the draft yet (see notarize-finish.yml)' })"
}
if (-not (Test-Path $updateJson)) {
    $problems += "$UpdateName is missing"
} elseif (-not (Test-Python @((Join-Path $ScriptRoot "build_update_parts.py"), "verify-release", "--version", $Version,
                                "--listing", $ListingFile, "--update-json", $updateJson))) {
    $problems += "the release files are incomplete, foreign or disagree with the update description"
}
if (-not $macosReady) { $problems += "macOS is not stapled yet" }
$wheelCheck = Get-Content (Join-Path $parts "wheel-check.json") -Raw | ConvertFrom-Json
if ($wheelCheck.needs_full_installer -and $body -notmatch "(?i)this update needs the full installer") {
    $problems += "a changed library has no wheel (or a Python changed), so the notes must say 'This update needs the full installer'"
}
$listing = Get-Content $ListingFile -Raw | ConvertFrom-Json
foreach ($item in $listing) { Write-Note ("{0,-46} {1,14:N0}  {2}" -f $item.name, $item.size, $item.sha256) }
if ($problems.Count -gt 0) {
    foreach ($problem in $problems) { Write-Host "   - $problem" -ForegroundColor Red }
    Stop-Release "the release is not ready to publish"
}
Write-Note "Every file is this run's, present, and agrees with the update description."

$BodyFile = Join-Path $WorkDir "body-current.md"
$NewBody = Join-Path $WorkDir "body-with-manifest.md"
Save-Text $BodyFile $body
Invoke-Python @((Join-Path $ScriptRoot "append_sha_manifest.py"), "--listing", $ListingFile, "--body-file", $BodyFile, "--write-body", $NewBody) | Out-Null
$installerHash = Get-Sha256 $Installer

if ($DryRun) {
    Write-Step "5. The SHA-256 block (dry run: not written to a release)"
    $text = [IO.File]::ReadAllText($NewBody)
    Write-Host $text.Substring($text.IndexOf("<!-- row-bot-update-manifest -->"))
    Write-Step "Dry run complete: $InstallerName built (sha256 $installerHash), $($listing.Count) release files checked, nothing signed or published"
    exit 0
}

# ── 5. Upload the installer and write the block ─────────────────────────────

Write-Step "5. Upload the installer and write the SHA-256 block"
Get-Draft | Out-Null
Invoke-Native -File $Gh -Arguments @("release", "upload", $Tag, $Installer, "-R", $Repository, "--clobber")
$release = Get-Release
$uploaded = @($release.assets | Where-Object { $_.name -eq $InstallerName })
if ($uploaded.Count -ne 1 -or ($uploaded[0].PSObject.Properties["digest"] -and $uploaded[0].digest -and $uploaded[0].digest -ne "sha256:$installerHash")) {
    Stop-Release "the installer on the draft is not the one signed here"
}
# tag_name every time: editing a draft without it detaches the draft from its tag ("untagged-…").
Invoke-Native -File $Gh -Arguments @("api", "-X", "PATCH", "repos/$Repository/releases/$script:ReleaseId",
    "-f", "tag_name=$Tag", "-F", "body=@$NewBody")
Write-Note "The block lists all $($listing.Count) files."

# ── 6. Publish ──────────────────────────────────────────────────────────────

Write-Step "6. Publish $Tag"
Write-Note "The signed installer: $InstallerName, sha256 $installerHash"
Write-Note "Draft: $($release.html_url)"
if ($PauseBeforePublish) {
    Write-Host "   Install the draft's $InstallerName on a test machine now (its sha256 is above)." -ForegroundColor Yellow
    Read-Host "   Press Enter when you have tried it" | Out-Null
}
$prerelease = $Version -match '-'
Write-Host "   Publishing makes $Tag the $(if ($prerelease) { 'newest beta' } else { 'latest release' }): Row-Bot" -ForegroundColor Yellow
Write-Host "   installs then offer it and download it. A published release is never changed." -ForegroundColor Yellow
$answer = Read-Host "   Type 'publish $Version' to publish now (anything else keeps the draft)"
if ($answer -ne "publish $Version") {
    Write-Note "Not published. The draft is complete; run this script again to publish it."
    exit 0
}
# The prompt may have waited a long time: the draft must still hold exactly what the block lists.
Get-Draft | Out-Null
$nowFile = Join-Path $WorkDir "assets-now.json"
Save-DraftAssets $nowFile
if (-not (Test-Python @($Publish, "same-assets", "--listing", $ListingFile, "--assets", $nowFile))) {
    Stop-Release "the draft changed since its block was written; run this script again"
}
Invoke-Native -File $Gh -Arguments @("api", "-X", "PATCH", "repos/$Repository/releases/$script:ReleaseId",
    "-f", "tag_name=$Tag", "-F", "draft=false", "-F", "prerelease=$(if ($prerelease) { 'true' } else { 'false' })",
    "-f", "make_latest=$(if ($prerelease) { 'false' } else { 'true' })")
$published = Get-Release
$afterFile = Join-Path $WorkDir "assets-published.json"
Save-DraftAssets $afterFile
$same = Test-Python @($Publish, "same-assets", "--listing", $ListingFile, "--assets", $afterFile)
if ($published.draft -or -not $same -or -not ([string]$published.body).Contains("row-bot-update-manifest")) {
    Write-Host "   The published release does not hold exactly the files its block lists. Mark it a pre-release" -ForegroundColor Red
    Write-Host "   (gh release edit $Tag --prerelease --latest=false) and see 'Pulling a bad release' in docs/RELEASING.md." -ForegroundColor Red
    throw "Published $Tag, but it changed while publishing"
}
Write-Step "Published $Tag"
Write-Note "update-manifest.yml now checks the block (it never overwrites it); container.yml builds the images"
Write-Note "and adds row-bot-container-$Version.json to the block."
