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
__version__ = "2.0.9"

# Security settings
MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB limit

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
        # Check request size before parsing
        content_length = request.content_length
        if content_length is not None and content_length > MAX_CONTENT_LENGTH:
            return web.json_response({
                "error": f"Request too large. Maximum size is {MAX_CONTENT_LENGTH // (1024*1024)} MB"
            }, status=413)
        
        # Get the workflow from the request
        json_data = await request.json()
        
        # Check if this is already in API format
        if WorkflowConverter.is_api_format(json_data):
            # Already in API format, return as-is with nice formatting
            return web.json_response(json_data, dumps=lambda x: json.dumps(x, ensure_ascii=False, indent=2))
        
        # Validate workflow structure
        if 'nodes' not in json_data or 'links' not in json_data:
            return web.json_response({
                "error": "Invalid workflow format - missing nodes or links"
            }, status=400)
        
        if not isinstance(json_data.get('nodes'), list):
            return web.json_response({
                "error": "Invalid workflow format - 'nodes' must be a list"
            }, status=400)
        
        if not isinstance(json_data.get('links'), list):
            return web.json_response({
                "error": "Invalid workflow format - 'links' must be a list"
            }, status=400)
        
        # Convert to API format
        num_nodes = len(json_data['nodes'])
        num_links = len(json_data['links'])

        api_format = WorkflowConverter.convert_to_api(json_data)

        num_converted = len(api_format)
        logger.info(f"[Workflow to API Converter v{__version__} by Seth A. Robinson] Converted workflow: {num_nodes} nodes, {num_links} links -> {num_converted} API nodes")

        # Return just the converted API format with proper Unicode encoding
        # This matches what "Save (API)" produces - just the nodes
        # Format with nice indentation for readability
        return web.json_response(api_format, dumps=lambda x: json.dumps(x, ensure_ascii=False, indent=2))
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in request: {e}")
        return web.json_response({
            'success': False,
            'error': f'Invalid JSON: {str(e)}'
        }, status=400)
        
    except Exception as e:
        import traceback
        # Log full traceback server-side only (don't expose to client)
        logger.error(f"Error converting workflow: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return web.json_response({
            "success": False,
            "error": "Internal server error during conversion"
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

# Define a marker node class for ComfyUI Manager compatibility
# Adding this node to a workflow helps ComfyUI Manager detect the dependency
class WorkflowToAPIConverterNode:
    """
    A marker node that indicates this workflow uses the Workflow-to-API Converter endpoint.
    
    Adding this node to your workflow helps ComfyUI Manager detect the dependency,
    so when someone imports your workflow, they'll be prompted to install this custom node.
    
    The actual conversion happens via the /workflow/convert API endpoint, not through this node.
    This node is purely informational and does not affect workflow execution.
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("info",)
    FUNCTION = "get_info"
    CATEGORY = "api"
    OUTPUT_NODE = False
    
    DESCRIPTION = "Marker node for ComfyUI Manager dependency detection. Adding this to your workflow helps others auto-install this custom node. The actual conversion uses the /workflow/convert API endpoint."
    
    def get_info(self):
        """Returns information about the converter endpoint."""
        info = f"Workflow to API Converter v{__version__}\nEndpoint: /workflow/convert\nby Seth A. Robinson"
        return (info,)

# Node class mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "WorkflowToAPIConverter": WorkflowToAPIConverterNode
}

# Display name mappings
NODE_DISPLAY_NAME_MAPPINGS = {
    "WorkflowToAPIConverter": "Workflow to API Converter (Marker)"
}

# Log that the endpoint has been registered
logger.info("Workflow to API converter endpoint registered at /workflow/convert")
print("[WorkflowToAPIConverter] API endpoint registered at /workflow/convert")