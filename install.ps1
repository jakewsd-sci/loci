# Install loci on Windows: uv (if needed), then the latest loci release as the `loci` command.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/jakewsd-sci/loci/main/install.ps1 | iex"
#
# uv installs Python 3.12 and loci's packages, at the versions loci was tested with, into a
# folder of their own; nothing else on the computer changes. `uv tool uninstall loci` removes it.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Windows PowerShell downloads slowly with a progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$repo = "jakewsd-sci/loci"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv, which installs Python and loci's packages..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$release = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest"
$wheel = $release.assets | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
$pins = $release.assets | Where-Object { $_.name -eq "constraints.txt" } | Select-Object -First 1
if (-not $wheel -or -not $pins) { throw "The latest loci release, $($release.tag_name), is incomplete." }

$constraints = Join-Path ([System.IO.Path]::GetTempPath()) "loci-constraints.txt"
Invoke-WebRequest $pins.browser_download_url -OutFile $constraints -UseBasicParsing
Write-Host "Installing loci $($release.tag_name)..."
uv tool install --force --python 3.12 --constraints $constraints $wheel.browser_download_url
if ($LASTEXITCODE -ne 0) { throw "uv could not install loci." }
uv tool update-shell *> $null

Write-Host ""
Write-Host "loci $($release.tag_name) is installed. Open a new terminal, then try:"
Write-Host "  loci demo                      a demo project with known answers"
Write-Host "  loci open C:\path\to\your\data  your own data"
