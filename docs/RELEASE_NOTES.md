loci runs geospatial analysis you can trust and trace, on a map: an agent that writes each
analysis as a script you review, checks every layer it makes, and records where each result
came from.

## Install

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/jakewsd-sci/loci/main/install.ps1 | iex"
```

**macOS or Linux**:

```sh
curl -LsSf https://raw.githubusercontent.com/jakewsd-sci/loci/main/install.sh | sh
```

Then, in a new terminal:

```sh
loci demo                         # a demo project with known answers
loci open path/to/your/data       # your own data
```

The first time, paste an API key into the model menu: an Anthropic key for Claude, or a key
for OpenAI, Google Gemini or OpenRouter. `loci doctor` checks the installation.

See the [README](https://github.com/jakewsd-sci/loci#readme) for more.
