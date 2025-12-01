"""
ComfyUI Workflow to API Converter - Custom Node
Adds a global API endpoint for converting workflows to API format
Created by Seth A. Robinson - https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint
"""

import json
import logging
from aiohttp import web
from .workflow_converter import WorkflowConverter

# Set up logging
logger = logging.getLogger(__name__)

# Module version
__version__ = "2.0.4"

# Import ComfyUI's PromptServer to register our endpoint
try:
    from server import PromptServer
except ImportError as e:
    logger.error("Could not import PromptServer. Make sure this is installed in ComfyUI's custom_nodes directory.")
    raise e

# Register the API endpoint when the custom node is loaded
@PromptServer.instance.routes.post('/workflow/convert')
async def convert_workflow_endpoint(request):
    """
    API endpoint to convert a non-API workflow to API format.
    
    Accepts POST request with JSON body containing the workflow.
    Returns the converted API format workflow.
    """
    try:
        # Get the workflow from the request
        json_data = await request.json()
        
        # Check if this is already in API format
        if WorkflowConverter.is_api_format(json_data):
            # Already in API format, return as-is with nice formatting
            return web.json_response(json_data, dumps=lambda x: json.dumps(x, ensure_ascii=False, indent=2))
        
        # Convert to API format
        if 'nodes' in json_data and 'links' in json_data:
            num_nodes = len(json_data.get('nodes', []))
            num_links = len(json_data.get('links', []))

            api_format = WorkflowConverter.convert_to_api(json_data)

            num_converted = len(api_format)
            logger.info(f"[Workflow to API Converter v{__version__} by Seth A. Robinson] Converted workflow: {num_nodes} nodes, {num_links} links -> {num_converted} API nodes")

            # Return just the converted API format with proper Unicode encoding
            # This matches what "Save (API)" produces - just the nodes
            # Format with nice indentation for readability
            return web.json_response(api_format, dumps=lambda x: json.dumps(x, ensure_ascii=False, indent=2))
        else:
            return web.json_response({
                "error": "Invalid workflow format - missing nodes or links"
            }, status=400)
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in request: {e}")
        return web.json_response({
            'success': False,
            'error': f'Invalid JSON: {str(e)}'
        }, status=400)
        
    except Exception as e:
        import traceback
        error_msg = f"Error converting workflow: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        return web.json_response({
            "success": False,
            "error": str(e),
            "details": traceback.format_exc()
        }, status=500)

# Also add a GET endpoint that provides information about the converter
@PromptServer.instance.routes.get('/workflow/convert')
async def converter_info(request):
    """
    GET endpoint that provides information about the workflow converter.
    """
    return web.json_response({
        'name': 'ComfyUI Workflow to API Converter',
        'version': __version__,
        'description': 'Converts non-API workflow format to API format for execution (now with nested subgraph support)',
        'usage': 'POST a workflow JSON to this endpoint to convert it to API format',
        'author': 'Seth A. Robinson',
        'repository': 'https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint'
    })

# Define a minimal node class for ComfyUI compatibility
# This node doesn't need to do anything since the main functionality is the API endpoint
class WorkflowToAPIConverterNode:
    """
    A placeholder node that enables the workflow-to-API converter endpoint.
    The actual conversion happens via the API endpoint, not through this node.
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "info": ("STRING", {
                    "default": "This node enables the /workflow/convert endpoint. No workflow interaction needed.",
                    "multiline": True
                })
            }
        }
    
    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "utils"
    OUTPUT_NODE = True
    
    def noop(self, info=None):
        """This node doesn't need to do anything - the API endpoint is already registered."""
        return {}

# Node class mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "WorkflowToAPIConverter": WorkflowToAPIConverterNode
}

# Display name mappings
NODE_DISPLAY_NAME_MAPPINGS = {
    "WorkflowToAPIConverter": "Workflow to API Converter (Endpoint Enabled)"
}

# Log that the endpoint has been registered
logger.info("Workflow to API converter endpoint registered at /workflow/convert")
print("[WorkflowToAPIConverter] API endpoint registered at /workflow/convert")