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

# The latest release, from where GitHub sends its address: unlike GitHub's API, this has no small
# limit for computers that share an address, as on a campus or office network.
$latest = [System.Net.WebRequest]::Create("https://github.com/$repo/releases/latest")
$latest.Method = "HEAD"
$response = $latest.GetResponse()
$tag = $response.ResponseUri.Segments[-1]
$response.Close()
if ($tag -notlike "v*") { throw "loci has no release yet." }
$download = "https://github.com/$repo/releases/download/$tag"

$constraints = Join-Path ([System.IO.Path]::GetTempPath()) "loci-constraints.txt"
Invoke-WebRequest "$download/constraints.txt" -OutFile $constraints -UseBasicParsing
Write-Host "Installing loci $tag..."
uv tool install --force --python 3.12 --constraints $constraints "$download/loci-$($tag.Substring(1))-py3-none-any.whl"
if ($LASTEXITCODE -ne 0) { throw "uv could not install loci." }
# uv reports on stderr, which Windows PowerShell turns into a stopping error when redirected.
$ErrorActionPreference = "Continue"
uv tool update-shell *> $null
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "loci $tag is installed. Open a new terminal, then try:"
Write-Host "  loci demo                      a demo project with known answers"
Write-Host "  loci open C:\path\to\your\data  your own data"
