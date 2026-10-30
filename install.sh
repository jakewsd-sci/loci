#!/bin/sh
# Install loci on macOS or Linux: uv (if needed), then the latest loci release as the `loci` command.
#
#   curl -LsSf https://raw.githubusercontent.com/jakewsd-sci/loci/main/install.sh | sh
#
# uv installs Python 3.12 and loci's packages, at the versions loci was tested with, into a
# folder of their own; nothing else on the computer changes. `uv tool uninstall loci` removes it.
set -eu
repo="jakewsd-sci/loci"

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv, which installs Python and loci's packages..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi

# The latest release, from where GitHub sends its address: unlike GitHub's API, this has no small
# limit for computers that share an address, as on a campus or office network.
latest=$(curl -LsSfI -o /dev/null -w '%{url_effective}' "https://github.com/$repo/releases/latest")
case "$latest" in
    */releases/tag/v*) tag=${latest##*/} ;;
    *) echo "loci has no release yet." >&2; exit 1 ;;
esac
download="https://github.com/$repo/releases/download/$tag"
wheel="$download/loci-${tag#v}-py3-none-any.whl"

constraints="${TMPDIR:-/tmp}/loci-constraints.txt"
curl -LsSf "$download/constraints.txt" -o "$constraints"
echo "Installing loci from $wheel..."
uv tool install --force --python 3.12 --constraints "$constraints" "$wheel"
uv tool update-shell >/dev/null 2>&1 || true

cat <<DONE

loci is installed. Open a new terminal, then try:
  loci demo                        a demo project with known answers
  loci open ~/path/to/your/data    your own data
DONE
