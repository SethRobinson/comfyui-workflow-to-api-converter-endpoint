# ComfyUI Workflow Converter Endpoint

## Overview
A ComfyUI custom node that adds a `/workflow/convert` endpoint to convert non-API workflow formats to API format. The "Save (API)" client-end Javascript logic has been converted to python so it can run server-side. 

## Why is this needed?

Ok, here's the deal, you export your workflow, and from your own custom app you call ComfyUI's prompt endpoint to execute it. Well, joke's on you because it gets an error.  Why?  You can't run workflows directly, you need to choose "Export (API)" which is an option you'll only see if you've enabled developer mode for some reason.

So you think, cool, I'll just use that.  Well, it works, but later you decide you need to edit your workflow a bit and you drag and drop your "API" version into ComfyUI and low and behold, it's a skeleton of a real workflow, it's missing stuff!  You you better hope you saved a copy of the ORIGINAL full workflow.

So this solution allows you to only work with "full workflows", you just have your app do the extra step of converting the workflow to an "API" version before using it.  In my app, I check filedates and cache it out.

The result?  I no longer have to manually save two versions of my workflows to use with the API, just the "full".

-Seth

## Disclaimer

How robust is this?  It handled all my workflows perfectly (even large 200 KB ones using Flux, Wan 2.2, etc) but your results may vary, no promises.  It has not been hardened or tested widely, so I don't recommend using this in any public-facing way.  

For example, there is no protection against someone trying to upload a 10 GB file, I don't know what would happen.  So I recommend only using this privately (same computer or private LAN) and for non-mission-critical stuff.

## Features
- Converts non-API workflows to the exact format produced by ComfyUI's "Save (API)" function
- Uses ComfyUI's actual node registry for accurate conversion
- Properly handles list and dictionary widget, subgraphs (including nested), etc
- Preserves Unicode characters (Chinese, Japanese, emojis, etc.) correctly
- Returns something that is ready to send to the `/prompt` endpoint, same as the original Export (API) option would create


## Version History

**V2.03** - Fixed conversion of bypassed kjnodes (WidgetToString, Number to Int, Text to Number) - no more "Exception when validating node" errors for bypassed nodes, target nodes now fall back to widget values when sources are bypassed (thanks to LORDofDOOM for PR #5)

**V2.02** - Fixed bug with subgraphs (thanks to v3i1y for reporting this bug), now also shows version # to the ComfyUI log when used

## Installation

### Method 1: Git Clone (Recommended)
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint
```

### Method 2: Manual Installation
1. Download this repository as a ZIP file
2. Extract the folder to `ComfyUI/custom_nodes/`
3. Rename the folder to `comfyui-workflow-to-api-converter-endpoint` (remove any `-main` suffix)


### After Installation
Restart ComfyUI and the `/workflow/convert` endpoint will be automatically available.

## Usage

The endpoint is automatically available after installation. No workflow changes needed - this isn't a node you put in a workflow.

Send a POST request to `/workflow/convert` with a non-API workflow JSON (for example, one you exported from ComfyUI using "Save" not "Save (API)") to get back the API format:

```python
import json
import requests

# Load a non-API workflow JSON from disk
with open("workflow.json", "r", encoding="utf-8") as f:
    workflow = json.load(f)

# Convert to API format
response = requests.post("http://localhost:8188/workflow/convert", json=workflow)
response.raise_for_status()
api_format = response.json()

# Optionally modify parameters in api_format here (e.g., seed, model, etc.)

# Execute the API-format workflow
execute_response = requests.post("http://localhost:8188/prompt", json={"prompt": api_format})
execute_response.raise_for_status()
```


### Using curl

- Convert a non-API `workflow.json` to API format and save as `api_workflow.json` (bash/zsh):

```bash
curl -s -X POST "http://localhost:8188/workflow/convert" \
  -H "Content-Type: application/json" \
  --data-binary @workflow.json > api_workflow.json
```

## Credits

**Created by Seth A. Robinson**

- **Website**: [rtsoft.com](https://rtsoft.com)
- **YouTube**: [@RobinsonTechnologies](https://youtube.com/@RobinsonTechnologies)
- **Twitter/X**: [@rtsoft](https://twitter.com/rtsoft)
- **Bluesky**: [@rtsoft.com](https://bsky.app/profile/rtsoft.com)
- **Mastodon**: [@rtsoft@mastodon.gamedev.place](https://mastodon.gamedev.place/@rtsoft)

*This project was developed with assistance from AI tools for code generation and documentation.*
