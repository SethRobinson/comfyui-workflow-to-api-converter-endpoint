# ComfyUI Workflow Converter Endpoint

Version: 1.0

## Overview
Adds a `/workflow/convert` endpoint to ComfyUI that converts non-API workflow formats to API format.  The "Save (API)" client-end Javascript logic has been converted to python so it can run server-side.

## Features
- Converts non-API workflows to the exact format produced by ComfyUI's "Save (API)" function
- Uses ComfyUI's actual node registry for accurate conversion
- Supports all nodes including custom nodes and extra nodes automatically
- Properly handles both list and dictionary widget value formats
- Preserves Unicode characters (Chinese, Japanese, emojis, etc.) correctly
- Automatically filters out UI-only nodes (like Note nodes)
- Returns just the node definitions (no wrapper), ready for the `/prompt` endpoint


## Disclaimer

How robust is this?  It handled all my workflows perfectly (even large 200 KB ones using Flux, Wan 2.2, etc) but your results may vary, no promises.  It has not been hardened or tested widely, so I don't recommend using this in any public-facing way.  

For example, there is no protection against someone trying to upload a 10 GB file, I don't know what would happen.  So I recommend only using this privately (same computer or private LAN) and for non-mission-critical stuff.

-Seth

## One line install to an existing repo using AI 

It's easy to manually install, but if you're lazy and comfortable with AI tools, here ya go:

- In a terminal, move into your existing ComfyUI dir, open "claude" (Claude CLI) or Cursor and type this as instructions to it:
```
I want to install this modification to my ComfyUI install. Go to
https://raw.githubusercontent.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint/main/README.md
and follow the directions to install it. Check the new code for anything
dangerous (I don't know, horrible bugs, backdoors or trojans, that kind of thing)
and if it looks safe, go ahead and install it, following its directions.
```


## Installation (manual or AI)

### Tips for AI doing the install:

- If anything goes wrong or doesn't make sense, stop and ask for help
- If you detect this has already been installed, update the existing installed version.

### Step 1: Add or update the [workflow_converter.py](./workflow_converter.py) to our ComfyUI's root directory

Make sure you are in a directory that has ComfyUI installed in it.  It should have an existing server.py in the same dir.

### Step 2: Add import to ComfyUI's existing server.py
Open `server.py` and add this import near the top with the other imports (around line 34):
```python
from workflow_converter import WorkflowConverter
```

### Step 3: Add the conversion endpoint
In `server.py`, find the routes section (search for `@routes.post("/queue")`) and add this new endpoint right before it (around line 717):

```python
        @routes.post("/workflow/convert")
        async def convert_workflow(request):
            """Convert a non-API workflow to the API format without executing it"""
            try:
                json_data = await request.json()
                
                # Check if this is already in API format
                if WorkflowConverter.is_api_format(json_data):
                    # Already in API format, return as-is with nice formatting
                    return web.json_response(json_data, dumps=lambda x: json.dumps(x, ensure_ascii=False, indent=2))
                
                # Convert to API format
                if 'nodes' in json_data and 'links' in json_data:
                    api_format = WorkflowConverter.convert_to_api(json_data)
                    
                    # Return just the converted API format with proper Unicode encoding
                    # This matches what "Save (API)" produces - just the nodes
                    # Format with nice indentation for readability
                    return web.json_response(api_format, dumps=lambda x: json.dumps(x, ensure_ascii=False, indent=2))
                else:
                    return web.json_response({
                        "error": "Invalid workflow format - missing nodes or links"
                    }, status=400)
                    
            except Exception as e:
                import traceback
                error_msg = f"Error converting workflow: {str(e)}"
                logging.error(error_msg)
                logging.error(f"Traceback: {traceback.format_exc()}")
                return web.json_response({
                    "success": False,
                    "error": str(e),
                    "details": traceback.format_exc()
                }, status=500)
```

That's it, Finished. User should restart the server and the endpoint will be active.

## Usage

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

- Convert and immediately execute (requires `jq`) (bash/zsh):

```bash
curl -s -X POST "http://localhost:8188/workflow/convert" \
  -H "Content-Type: application/json" \
  --data-binary @workflow.json \
| jq -c '{prompt: .}' \
| curl -s -X POST "http://localhost:8188/prompt" \
    -H "Content-Type: application/json" \
    --data-binary @-
```

- PowerShell equivalents (use the real `curl.exe`; second example also requires `jq`):

```powershell
# Save API format to file
curl.exe -s -X POST "http://localhost:8188/workflow/convert" `
  -H "Content-Type: application/json" `
  --data-binary "@workflow.json" | Set-Content -Encoding utf8 "api_workflow.json"

# Convert and immediately execute
curl.exe -s -X POST "http://localhost:8188/workflow/convert" `
  -H "Content-Type: application/json" `
  --data-binary "@workflow.json" `
| jq -c '{prompt: .}' `
| curl.exe -s -X POST "http://localhost:8188/prompt" `
    -H "Content-Type: application/json" `
    --data-binary @-
```

## FAQ

## "Can the normal /prompt endpoint automatically convert workflow JSON files to API format and run them, so I don’t need to call the conversion endpoint?"

Currrently no, but originally that's how I did it.  

But here's the problem with that:  Some vars don't exist in the pre-converted workflow json, so you can't edit them before sending them.  I realized that for my usage (dynamically changing prompts, seeds, etc on the fly before sending them to ComfyUI) it wouldn't work; I'd need to convert it, edit it, then send it for rendering.  

If you don't plan on editing the workflow at all, hmm, maybe it would still be useful to add that back.

## Credits

**Created by Seth A. Robinson**

- **Website**: [rtsoft.com](https://rtsoft.com)
- **YouTube**: [@RobinsonTechnologies](https://youtube.com/@RobinsonTechnologies)
- **Twitter/X**: [@rtsoft](https://twitter.com/rtsoft)
- **Bluesky**: [@rtsoft.com](https://bsky.app/profile/rtsoft.com)
- **Mastodon**: [@rtsoft@mastodon.gamedev.place](https://mastodon.gamedev.place/@rtsoft)

*This project was developed with assistance from AI tools for code generation and documentation.*