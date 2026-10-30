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

## What's in it

- **A map app on your own folder.** Layers, a code editor, lineage and an agent, served on this
  computer only. Step through time stacks, chart a place's values through time, draw an area to
  ask about, and see failed checks on the map.
- **Your data, in and out.** The folder's files are listed for a click onto the map; drop more
  on the map; export any layer, with a record of where it came from. GeoTIFF, GeoPackage,
  GeoJSON, shapefile, GeoParquet, CSV points, NetCDF and Zarr (ERA5 from the cloud included),
  and Sentinel-2 from Earth Search.
- **An agent you can check.** Claude, or a model from OpenAI, Gemini or OpenRouter, writes each
  analysis as a script in `analysis/`. Changes to your scripts wait for you as diffs. Every
  layer is validated as it is made (CRS, extents, grids, nodata, value ranges, geometry), and
  assumptions are flagged, not hidden.
- **Scripts in a sandbox.** Each script writes only in its run folder, starts no programs,
  reaches only its inputs on the network, and cannot read your keys. loci's guard does this
  everywhere. On macOS and Linux, installing Anthropic's sandbox runtime
  (`npm install -g @anthropic-ai/sandbox-runtime`) adds the operating system's own limits.
- **A demo with known answers** (`loci demo`): five questions, each with a trap that a
  careless answer falls into.

See the [README](https://github.com/jakewsd-sci/loci#readme) for more.
