"""
Workflow converter for ComfyUI
Converts non-API workflow format to API format for execution
Created by Seth A. Robinson - https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint
"""

import json
import logging
from typing import Dict, Any, List, Tuple, Optional, Union

# Set up logging
logger = logging.getLogger(__name__)

# Import ComfyUI node information - this is required
try:
    import nodes
except ImportError as e:
    raise ImportError(
        "Cannot import ComfyUI nodes module. "
        "This converter must be run within the ComfyUI environment. "
        "Make sure ComfyUI is properly initialized before using the converter."
    ) from e

# Cache for node definitions
_node_info_cache = {}

def get_node_info_for_type(node_type: str) -> Dict[str, Any]:
    """Get node information for a specific node type"""
    global _node_info_cache
    
    if node_type not in _node_info_cache:
        # Try to get the node info
        if node_type in nodes.NODE_CLASS_MAPPINGS:
            try:
                obj_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                info = {}
                info['input'] = obj_class.INPUT_TYPES()
                info['input_order'] = {key: list(value.keys()) for (key, value) in obj_class.INPUT_TYPES().items()}
                _node_info_cache[node_type] = info
            except Exception as e:
                logger.debug(f"Could not get node info for {node_type}: {e}")
                _node_info_cache[node_type] = None
        else:
            _node_info_cache[node_type] = None
    
    return _node_info_cache.get(node_type)


class WorkflowConverter:
    """Converts non-API workflow format to API prompt format"""
    
    @staticmethod
    def is_api_format(workflow: Dict[str, Any]) -> bool:
        """
        Check if a workflow is already in API format.
        API format has node IDs as keys with 'class_type' and 'inputs'.
        Non-API format has 'nodes', 'links', etc.
        """
        # Check for non-API format indicators
        if 'nodes' in workflow and 'links' in workflow:
            return False
        
        # Check if it looks like API format
        # API format should have numeric string keys with class_type
        for key, value in workflow.items():
            if key in ['prompt', 'extra_data', 'client_id']:
                continue
            if isinstance(value, dict) and 'class_type' in value:
                return True
        
        return False
    
    @staticmethod
    def convert_to_api(workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a non-API workflow to API format.
        
        Args:
            workflow: Non-API format workflow with nodes and links
            
        Returns:
            API format workflow ready for execution
        """
        if WorkflowConverter.is_api_format(workflow):
            # Already in API format
            return workflow
        
        # Extract nodes and links
        nodes = workflow.get('nodes', [])
        links = workflow.get('links', [])
        
        # Build link map for quick lookup
        # link_id -> (source_node_id, source_slot, target_node_id, target_slot, type)
        link_map = {}
        for link in links:
            if len(link) >= 6:
                link_id = link[0]
                source_id = link[1]
                source_slot = link[2]
                target_id = link[3]
                target_slot = link[4]
                link_type = link[5] if len(link) > 5 else None
                link_map[link_id] = {
                    'source_id': source_id,
                    'source_slot': source_slot,
                    'target_id': target_id,
                    'target_slot': target_slot,
                    'type': link_type
                }
        
        # First pass: identify PrimitiveNodes and their values
        primitive_values = {}
        for node in nodes:
            if node.get('type') == 'PrimitiveNode':
                node_id = str(node.get('id'))
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    primitive_values[node_id] = widget_values[0]
        
        # Build API format prompt
        api_prompt = {}
        
        for node in nodes:
            node_id = str(node.get('id'))
            node_type = node.get('type')
            
            if not node_type:
                continue
                
            # Skip muted nodes
            if node.get('mode', 0) == 2:  # Mode 2 is muted
                continue
            
            # Skip non-executable nodes (like Note nodes and PrimitiveNodes)
            # These are UI-only nodes that don't need to be in the API format
            if node_type in ['Note', 'PrimitiveNode']:
                logger.info(f"Skipping {node_type} node {node_id}")
                continue
            
            # Build node entry
            api_node = {
                'inputs': {},
                'class_type': node_type
            }
            
            # Add _meta field with title if available
            if 'title' in node:
                api_node['_meta'] = {'title': node['title']}
            elif hasattr(nodes, 'NODE_DISPLAY_NAME_MAPPINGS') and node_type in nodes.NODE_DISPLAY_NAME_MAPPINGS:
                # Use ComfyUI's node display name mappings
                api_node['_meta'] = {'title': nodes.NODE_DISPLAY_NAME_MAPPINGS[node_type]}
            else:
                # Use the node type as-is for the title
                api_node['_meta'] = {'title': node_type}
            
            # Process inputs (connections via links)
            link_inputs = {}
            primitive_inputs = {}  # Separate tracking for resolved primitive values
            node_inputs = node.get('inputs', [])
            
            if node_inputs:
                for input_info in node_inputs:
                    input_name = input_info.get('name')
                    input_link = input_info.get('link')
                    
                    if input_link is not None and input_link in link_map:
                        # This input is connected via a link
                        link_data = link_map[input_link]
                        source_node_id = str(link_data['source_id'])
                        source_slot = link_data['source_slot']
                        
                        # Check if the source is a PrimitiveNode
                        if source_node_id in primitive_values:
                            # This is a resolved primitive value - treat as widget for ordering
                            primitive_inputs[input_name] = primitive_values[source_node_id]
                        else:
                            # Keep as link
                            link_inputs[input_name] = [source_node_id, source_slot]
            
            # Get the correct input order from the node class
            ordered_inputs = WorkflowConverter._get_ordered_inputs(node_type, node)
            
            # Process widget values
            widget_values = node.get('widgets_values')
            widget_inputs = {}
            
            if widget_values is not None:
                # Handle both list and dict widget values
                if isinstance(widget_values, dict):
                    # Direct dictionary mapping - use as-is
                    for key, value in widget_values.items():
                        # Skip special keys that aren't actual inputs
                        if key in ['videopreview', 'preview']:
                            continue
                        # Only add if not connected via link
                        if key not in link_inputs:
                            widget_inputs[key] = value
                            
                elif isinstance(widget_values, list):
                    # List of values - need to map to widget names
                    widget_mappings = WorkflowConverter._get_widget_mappings(node_type, node)
                    
                    # Special handling for control_after_generate values
                    filtered_values = WorkflowConverter._filter_control_values(widget_values)
                    
                    # Map values to widget names
                    if widget_mappings:
                        for i, value in enumerate(filtered_values):
                            if i < len(widget_mappings):
                                widget_name = widget_mappings[i]
                                # Only add if we have a valid name and it's not connected via link
                                if widget_name and widget_name not in link_inputs:
                                    widget_inputs[widget_name] = value
                    else:
                        # If we couldn't determine mappings for an unknown node,
                        # we'll have to skip the widget values
                        if filtered_values:
                            logger.warning(f"Could not map widget values for unknown node type '{node_type}' (node {node_id})")
            
            # Build inputs in the correct order
            # The official "Save (API)" format follows this pattern:
            # ALL widget values first (in node class order), then ALL link inputs (in node class order)
            # Note: Resolved primitive values are treated as widgets for ordering
            if ordered_inputs:
                # First pass: add all widget values in order (including resolved primitives)
                for input_name in ordered_inputs:
                    if input_name in widget_inputs:
                        api_node['inputs'][input_name] = widget_inputs[input_name]
                    elif input_name in primitive_inputs:
                        api_node['inputs'][input_name] = primitive_inputs[input_name]
                
                # Second pass: add all link inputs in order
                for input_name in ordered_inputs:
                    if input_name in link_inputs and input_name not in api_node['inputs']:
                        api_node['inputs'][input_name] = link_inputs[input_name]
                
                # Add any remaining inputs that weren't in the ordered list
                for key, value in widget_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in primitive_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in link_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
            else:
                # Fallback when we don't have the node class: add all inputs in order they appear
                # First add ALL widget inputs and primitives, then ALL link inputs
                for key, value in widget_inputs.items():
                    api_node['inputs'][key] = value
                for key, value in primitive_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in link_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
            
            api_prompt[node_id] = api_node
        
        return api_prompt
    
    @staticmethod
    def _filter_control_values(widget_values: List[Any]) -> List[Any]:
        """Filter out control_after_generate values from widget list"""
        filtered = []
        skip_next = False
        
        for i, value in enumerate(widget_values):
            if skip_next:
                skip_next = False
                continue
                
            # Check if this is a control value that should be skipped
            if value in ['fixed', 'increment', 'decrement', 'randomize']:
                # This is a control_after_generate value, skip it
                continue
            
            # Check if this value is followed by a control value
            if i + 1 < len(widget_values):
                next_val = widget_values[i + 1]
                if next_val in ['fixed', 'increment', 'decrement', 'randomize']:
                    # This widget has a control value after it
                    filtered.append(value)
                    # The control value will be skipped in the next iteration
                    continue
            
            filtered.append(value)
        
        return filtered
    
    @staticmethod
    def _get_ordered_inputs(node_type: str, node: Dict[str, Any]) -> List[str]:
        """
        Get the ordered list of all inputs (both widgets and connections) for a node type.
        Returns the inputs in the order they should appear in the API format.
        """
        # Try to get input order from node properties first
        properties = node.get('properties', {})
        if 'Node name for S&R' in properties:
            # Sometimes the actual node class name is stored here
            node_type = properties['Node name for S&R']
        
        # Try to get from cached node info first
        node_info = get_node_info_for_type(node_type)
        if node_info and 'input_order' in node_info:
            input_order = node_info['input_order']
            input_names = []
            for section in ['required', 'optional']:
                if section in input_order:
                    input_names.extend(input_order[section])
            if input_names:
                return input_names
        
        # Fallback: Get input order from actual node class if it's loaded
        if hasattr(nodes, 'NODE_CLASS_MAPPINGS') and node_type in nodes.NODE_CLASS_MAPPINGS:
            try:
                node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                input_types = node_class.INPUT_TYPES()
                
                # Build ordered list of all input names
                input_names = []
                
                # Process required inputs first, then optional
                for section in ['required', 'optional']:
                    if section in input_types:
                        for input_name in input_types[section].keys():
                            input_names.append(input_name)
                
                if input_names:
                    return input_names
            except Exception as e:
                logger.debug(f"Could not get input order from node class for {node_type}: {e}")
        
        # For unknown nodes, return empty list (will use fallback ordering)
        return []
    
    @staticmethod
    def _get_widget_mappings(node_type: str, node: Dict[str, Any]) -> List[Optional[str]]:
        """
        Get widget name mappings for a given node type.
        Returns a list of widget names in the order they appear.
        
        This is used to map the widget_values list to actual input names.
        """
        # Try to get widget order from node properties first
        properties = node.get('properties', {})
        if 'Node name for S&R' in properties:
            # Sometimes the actual node class name is stored here
            node_type = properties['Node name for S&R']
        
        # Try to get from cached node info first
        node_info = get_node_info_for_type(node_type)
        if node_info and 'input' in node_info:
            try:
                input_def = node_info['input']
                widget_names = []
                
                # Process required inputs first, then optional
                for section in ['required', 'optional']:
                    if section in input_def:
                        for input_name, input_spec in input_def[section].items():
                            # Check if this is a widget input (not a node connection)
                            if isinstance(input_spec, (list, tuple)) and len(input_spec) >= 1:
                                input_type = input_spec[0]
                                # Check if it's a widget type
                                if isinstance(input_type, list):
                                    # This is a combo box widget (list of choices)
                                    widget_names.append(input_name)
                                elif input_type in ['INT', 'FLOAT', 'STRING', 'BOOLEAN']:
                                    # Basic widget types
                                    widget_names.append(input_name)
                                elif isinstance(input_type, str) and not input_type.isupper():
                                    # Custom widget types (lowercase)
                                    widget_names.append(input_name)
                                # Skip connection types (MODEL, LATENT, CONDITIONING, etc.)
                
                if widget_names:
                    return widget_names
            except Exception as e:
                logger.debug(f"Could not get widget mappings from node info for {node_type}: {e}")
        
        # Fallback: Get widget order from actual node class if it's loaded
        if hasattr(nodes, 'NODE_CLASS_MAPPINGS') and node_type in nodes.NODE_CLASS_MAPPINGS:
            try:
                node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                input_types = node_class.INPUT_TYPES()
                
                # Build ordered list of widget names (non-connection inputs)
                widget_names = []
                
                # Process required inputs first, then optional
                for section in ['required', 'optional']:
                    if section in input_types:
                        for input_name, input_spec in input_types[section].items():
                            # Check if this is a widget input (not a node connection)
                            if isinstance(input_spec, tuple) and len(input_spec) >= 1:
                                input_type = input_spec[0]
                                # Check if it's a widget type
                                if isinstance(input_type, list):
                                    # This is a combo box widget (list of choices)
                                    widget_names.append(input_name)
                                elif input_type in ['INT', 'FLOAT', 'STRING', 'BOOLEAN']:
                                    # Basic widget types
                                    widget_names.append(input_name)
                                elif isinstance(input_type, str) and not input_type.isupper():
                                    # Custom widget types (lowercase)
                                    widget_names.append(input_name)
                                # Skip connection types (MODEL, LATENT, CONDITIONING, etc.)
                
                if widget_names:
                    return widget_names
            except Exception as e:
                logger.debug(f"Could not get widget mappings from node class for {node_type}: {e}")
        
        # Fallback: Try to infer widget mappings from the workflow and widget values
        widget_values = node.get('widgets_values', [])
        if not isinstance(widget_values, list) or len(widget_values) == 0:
            return []
        
        # Get all inputs from the node
        all_inputs = []
        connected_inputs = set()
        widget_flagged_inputs = []
        
        for input_info in node.get('inputs', []):
            input_name = input_info.get('name')
            if input_name:
                all_inputs.append(input_name)
                if input_info.get('link') is not None:
                    connected_inputs.add(input_name)
                if input_info.get('widget'):
                    widget_flagged_inputs.append(input_name)
        
        # If we have widget-flagged inputs, start with those
        if widget_flagged_inputs:
            # But we might have more widget values than flagged inputs
            # This happens with nodes like WanImageToVideo where not all widgets are flagged
            if len(widget_values) > len(widget_flagged_inputs):
                # We need to find the additional widget inputs
                # They should be the non-connected inputs that aren't flagged
                potential_widgets = [inp for inp in all_inputs 
                                   if inp not in connected_inputs and inp not in widget_flagged_inputs]
                # Combine them in order
                return widget_flagged_inputs + potential_widgets[:len(widget_values) - len(widget_flagged_inputs)]
            return widget_flagged_inputs
        
        # No flagged widgets, try to guess based on which inputs aren't connected
        unconnected = [inp for inp in all_inputs if inp not in connected_inputs]
        if unconnected and len(unconnected) >= len(widget_values):
            return unconnected[:len(widget_values)]
        
        # If we still can't determine mappings, return empty
        return []


def save_json_with_encoding(data: Dict[str, Any], filepath: str, ensure_ascii: bool = False):
    """Save JSON with proper Unicode handling"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)