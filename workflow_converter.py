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
    def is_subgraph_uuid(node_type: str) -> bool:
        """
        Check if a node type is a subgraph UUID.
        Subgraphs are identified by UUID format (e.g., "b43bb7e6-178c-4f1a-b014-ac4d6a50fca2")
        """
        if not node_type or not isinstance(node_type, str):
            return False
        # UUIDs are 36 characters with dashes at positions 8, 13, 18, 23
        if len(node_type) == 36 and node_type.count('-') == 4:
            parts = node_type.split('-')
            if len(parts) == 5 and all(len(p) in [8, 4, 4, 4, 12] for i, p in enumerate(parts) if i == 0 or i == 4 or len(p) == 4):
                return True
        return False

    @staticmethod
    def expand_subgraph(subgraph_node_id: int, subgraph_def: Dict[str, Any], workflow_links: List) -> Tuple[List[Dict], List]:
        """
        Expand a subgraph into individual nodes.

        Args:
            subgraph_node_id: The ID of the subgraph node in the main workflow
            subgraph_def: The subgraph definition from definitions.subgraphs
            workflow_links: The links from the main workflow

        Returns:
            Tuple of (expanded_nodes, expanded_links)
        """
        expanded_nodes = []
        expanded_links = []

        # Get subgraph internal nodes and links
        internal_nodes = subgraph_def.get('nodes', [])
        internal_links = subgraph_def.get('links', [])

        # Find the maximum link ID in the existing workflow links to avoid conflicts
        # Subgraph internal links may reuse IDs that already exist in the workflow
        max_link_id = 0
        for link in workflow_links:
            if isinstance(link, (list, tuple)) and len(link) > 0:
                link_id = link[0]
                if isinstance(link_id, int) and link_id > max_link_id:
                    max_link_id = link_id

        # Create a remapping for internal link IDs to avoid conflicts
        link_id_remap = {}
        next_link_id = max_link_id + 1
        for link in internal_links:
            if isinstance(link, dict):
                old_id = link.get('id')
                if old_id is not None:
                    link_id_remap[old_id] = next_link_id
                    next_link_id += 1

        # Build a mapping of internal link IDs to link data
        internal_link_map = {}
        for link in internal_links:
            if isinstance(link, dict):
                link_id = link.get('id')
                internal_link_map[link_id] = link

        # Build input/output mappings for the subgraph
        # Inputs: map input slot index to list of (internal_node_id, internal_slot) tuples
        # (one input may connect to multiple internal nodes)
        # Outputs: map (internal_node_id, internal_slot) to output slot index
        subgraph_inputs = subgraph_def.get('inputs', [])
        subgraph_outputs = subgraph_def.get('outputs', [])

        input_slot_to_internal = {}  # slot_index -> [(target_node_id, target_slot), ...]
        internal_to_output_slot = {}  # (source_node_id, source_slot) -> slot_index

        # Map inputs from the inputNode (-10) to actual internal nodes
        for idx, input_def in enumerate(subgraph_inputs):
            # Find links from inputNode to internal nodes
            input_link_ids = input_def.get('linkIds', [])
            targets = []
            for link_id in input_link_ids:
                if link_id in internal_link_map:
                    link = internal_link_map[link_id]
                    target_id = link.get('target_id')
                    target_slot = link.get('target_slot')
                    targets.append((target_id, target_slot))
            if targets:
                input_slot_to_internal[idx] = targets

        # Map outputs from internal nodes to the outputNode (-20)
        for idx, output_def in enumerate(subgraph_outputs):
            # Find links from internal nodes to outputNode
            output_link_ids = output_def.get('linkIds', [])
            for link_id in output_link_ids:
                if link_id in internal_link_map:
                    link = internal_link_map[link_id]
                    origin_id = link.get('origin_id')
                    origin_slot = link.get('origin_slot')
                    internal_to_output_slot[(origin_id, origin_slot)] = idx

        # Create expanded nodes with prefixed IDs
        for node in internal_nodes:
            internal_id = node.get('id')
            expanded_node = node.copy()
            # Prefix the node ID with the subgraph node ID
            expanded_node['id'] = f"{subgraph_node_id}:{internal_id}"

            # Update the node's inputs to remove links from the inputNode (-10)
            # and remap internal link IDs to avoid conflicts
            if 'inputs' in expanded_node:
                updated_inputs = []
                for input_info in expanded_node['inputs']:
                    input_link = input_info.get('link')
                    # Check if this link comes from the inputNode
                    if input_link in internal_link_map:
                        link_data = internal_link_map[input_link]
                        if link_data.get('origin_id') == -10:
                            # This input comes from the subgraph's inputNode
                            # Remove the link - it will be replaced by an external connection
                            input_copy = input_info.copy()
                            input_copy['link'] = None
                            updated_inputs.append(input_copy)
                        else:
                            # Internal connection - remap the link ID to avoid conflicts
                            input_copy = input_info.copy()
                            if input_link in link_id_remap:
                                input_copy['link'] = link_id_remap[input_link]
                            updated_inputs.append(input_copy)
                    else:
                        # No link or unknown link, keep as-is
                        updated_inputs.append(input_info)
                expanded_node['inputs'] = updated_inputs

            expanded_nodes.append(expanded_node)

        # Create expanded links
        # First, handle internal links (between nodes inside the subgraph)
        for link in internal_links:
            if isinstance(link, dict):
                origin_id = link.get('origin_id')
                target_id = link.get('target_id')

                # Skip links from/to input/output nodes (-10, -20)
                if origin_id in [-10, -20] or target_id in [-10, -20]:
                    continue

                # Get the remapped link ID to avoid conflicts with workflow links
                old_link_id = link.get('id')
                new_link_id = link_id_remap.get(old_link_id, old_link_id)

                # Create new link with prefixed node IDs and remapped link ID
                expanded_link = [
                    new_link_id,  # remapped link_id to avoid conflicts
                    f"{subgraph_node_id}:{origin_id}",  # source with prefix
                    link.get('origin_slot'),
                    f"{subgraph_node_id}:{target_id}",  # target with prefix
                    link.get('target_slot'),
                    link.get('type')
                ]
                expanded_links.append(expanded_link)

        return expanded_nodes, expanded_links, input_slot_to_internal, internal_to_output_slot

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
        workflow_nodes = workflow.get('nodes', [])
        links = workflow.get('links', [])

        # Extract subgraph definitions
        subgraph_defs = {}
        definitions = workflow.get('definitions', {})
        for subgraph in definitions.get('subgraphs', []):
            subgraph_id = subgraph.get('id')
            if subgraph_id:
                subgraph_defs[subgraph_id] = subgraph

        # Expand subgraphs into individual nodes
        # We need to do this recursively to handle nested subgraphs
        # Keep expanding until no more subgraphs are found
        subgraph_input_mappings = {}  # subgraph_node_id -> {subgraph_input_idx: [(internal_node_id, internal_slot), ...]}
        subgraph_output_mappings = {}  # subgraph_node_id -> {(internal_node_id, internal_slot): slot_idx}
        # Maps outer node input slot to subgraph definition input index (by name matching)
        subgraph_slot_to_input_idx = {}  # subgraph_node_id -> {outer_slot: subgraph_input_idx}

        max_iterations = 10  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            expanded_nodes = []
            found_subgraph = False

            for node in workflow_nodes:
                node_type = node.get('type')
                node_id = node.get('id')

                # Check if this is a subgraph node
                if WorkflowConverter.is_subgraph_uuid(node_type) and node_type in subgraph_defs:
                    found_subgraph = True
                    logger.debug(f"Expanding subgraph node {node_id} (type: {node_type}) - iteration {iteration}")
                    # Expand the subgraph
                    subgraph_def = subgraph_defs[node_type]
                    sg_nodes, sg_links, input_map, output_map = WorkflowConverter.expand_subgraph(
                        node_id, subgraph_def, links
                    )

                    # Add expanded nodes to our list
                    expanded_nodes.extend(sg_nodes)

                    # Add internal links from the subgraph
                    links.extend(sg_links)

                    # Store input/output mappings for later link remapping
                    # Use string representation of node_id for consistency
                    subgraph_input_mappings[str(node_id)] = input_map
                    subgraph_output_mappings[str(node_id)] = output_map
                    
                    # Build mapping from outer node input slots to subgraph input indices
                    # The outer node's inputs may have different slot positions than the subgraph definition
                    # We match by input name to find the correct subgraph input index
                    outer_inputs = node.get('inputs', [])
                    subgraph_inputs = subgraph_def.get('inputs', [])
                    
                    # Build name -> subgraph_input_idx map
                    subgraph_input_name_to_idx = {}
                    for idx, sg_input in enumerate(subgraph_inputs):
                        input_name = sg_input.get('name')
                        if input_name:
                            subgraph_input_name_to_idx[input_name] = idx
                    
                    # Map outer slots to subgraph input indices
                    slot_mapping = {}
                    for outer_slot, outer_input in enumerate(outer_inputs):
                        outer_name = outer_input.get('name')
                        if outer_name and outer_name in subgraph_input_name_to_idx:
                            slot_mapping[outer_slot] = subgraph_input_name_to_idx[outer_name]
                            logger.debug(f"Subgraph {node_id}: outer slot {outer_slot} ('{outer_name}') -> subgraph input idx {subgraph_input_name_to_idx[outer_name]}")
                    
                    subgraph_slot_to_input_idx[str(node_id)] = slot_mapping
                else:
                    # Regular node, keep as-is
                    expanded_nodes.append(node)

            # Replace workflow_nodes with expanded version
            workflow_nodes = expanded_nodes

            # If we didn't find any subgraphs in this iteration, we're done
            if not found_subgraph:
                logger.debug(f"Subgraph expansion complete after {iteration} iteration(s)")
                break

        if iteration >= max_iterations:
            logger.warning(f"Reached maximum subgraph expansion iterations ({max_iterations}). There may be circular subgraph references.")

        # Helper function to recursively resolve subgraph outputs
        def resolve_subgraph_output(node_id_str, slot, depth=0):
            """
            Recursively resolve a subgraph output to the actual internal node.
            Returns (resolved_node_id, resolved_slot)
            """
            # Prevent excessive recursion (security hardening)
            if depth > 100:
                logger.warning(f"Max recursion depth reached resolving subgraph output for node {node_id_str}")
                return (node_id_str, slot)
            
            if node_id_str in subgraph_output_mappings:
                output_map = subgraph_output_mappings[node_id_str]
                for (internal_node, internal_slot), out_slot in output_map.items():
                    if out_slot == slot:
                        # Found the internal node for this output
                        # Construct the new node ID
                        new_node_id = f"{node_id_str}:{internal_node}"
                        # Recursively resolve in case this is also a subgraph
                        return resolve_subgraph_output(new_node_id, internal_slot, depth + 1)
            # Not a subgraph or not found in mappings, return as-is
            return (node_id_str, slot)

        # Helper function to recursively resolve subgraph inputs
        def resolve_subgraph_input(node_id_str, slot, depth=0):
            """
            Recursively resolve a subgraph input to the actual internal node.
            Returns (resolved_node_id, resolved_slot)
            Note: When there are multiple targets, returns the first one.
            Use resolve_subgraph_input_all for all targets.
            """
            # Prevent excessive recursion (security hardening)
            if depth > 100:
                logger.warning(f"Max recursion depth reached resolving subgraph input for node {node_id_str}")
                return (node_id_str, slot)
            
            if node_id_str in subgraph_input_mappings:
                input_map = subgraph_input_mappings[node_id_str]
                
                # Convert outer slot to subgraph input index using the slot mapping
                subgraph_input_idx = slot
                if node_id_str in subgraph_slot_to_input_idx:
                    slot_mapping = subgraph_slot_to_input_idx[node_id_str]
                    if slot in slot_mapping:
                        subgraph_input_idx = slot_mapping[slot]
                
                if subgraph_input_idx in input_map:
                    targets = input_map[subgraph_input_idx]
                    if targets:
                        internal_node, internal_slot = targets[0]
                        # Construct the new node ID
                        new_node_id = f"{node_id_str}:{internal_node}"
                        # Recursively resolve in case this is also a subgraph
                        return resolve_subgraph_input(new_node_id, internal_slot, depth + 1)
            # Not a subgraph or not found in mappings, return as-is
            return (node_id_str, slot)
        
        # Helper function to get ALL internal targets for a subgraph input
        def resolve_subgraph_input_all(node_id_str, slot, depth=0):
            """
            Recursively resolve a subgraph input to ALL actual internal nodes.
            Returns list of (resolved_node_id, resolved_slot) tuples.
            """
            if depth > 100:
                logger.warning(f"Max recursion depth reached resolving subgraph input for node {node_id_str}")
                return [(node_id_str, slot)]
            
            if node_id_str in subgraph_input_mappings:
                input_map = subgraph_input_mappings[node_id_str]
                
                # Convert outer slot to subgraph input index using the slot mapping
                subgraph_input_idx = slot
                if node_id_str in subgraph_slot_to_input_idx:
                    slot_mapping = subgraph_slot_to_input_idx[node_id_str]
                    if slot in slot_mapping:
                        subgraph_input_idx = slot_mapping[slot]
                
                if subgraph_input_idx in input_map:
                    targets = input_map[subgraph_input_idx]
                    results = []
                    for internal_node, internal_slot in targets:
                        new_node_id = f"{node_id_str}:{internal_node}"
                        # Recursively resolve in case this is also a subgraph
                        results.extend(resolve_subgraph_input_all(new_node_id, internal_slot, depth + 1))
                    return results if results else [(node_id_str, slot)]
            # Not a subgraph or not found in mappings, return as-is
            return [(node_id_str, slot)]

        # Update links to handle subgraph inputs and outputs
        # Also track which expanded node inputs need to be updated
        node_input_updates = {}  # expanded_node_id -> {input_name: link_id}

        updated_links = []
        for link in links:
            if len(link) >= 6:
                link_id = link[0]
                source_id = link[1]
                source_slot = link[2]
                target_id = link[3]
                target_slot = link[4]
                link_type = link[5] if len(link) > 5 else None

                # Recursively resolve subgraph source
                source_id_str = str(source_id)
                source_id, source_slot = resolve_subgraph_output(source_id_str, source_slot)

                # Recursively resolve subgraph target(s) - one input may go to multiple internal nodes
                target_id_str = str(target_id)
                all_targets = resolve_subgraph_input_all(target_id_str, target_slot)
                
                # Track node input updates for ALL targets that were remapped
                for resolved_target_id, resolved_target_slot in all_targets:
                    if resolved_target_id != target_id_str:
                        if resolved_target_id not in node_input_updates:
                            node_input_updates[resolved_target_id] = {}
                        node_input_updates[resolved_target_id][resolved_target_slot] = link_id
                
                # Use the first target for the updated link
                resolved_target_id, resolved_target_slot = all_targets[0]
                target_id = resolved_target_id
                target_slot = resolved_target_slot

                updated_links.append([link_id, source_id, source_slot, target_id, target_slot, link_type])

        # Replace links with updated version
        links = updated_links

        # Update the expanded nodes' inputs to reference the external link IDs
        for node in workflow_nodes:
            node_id_str = str(node.get('id'))
            if node_id_str in node_input_updates and 'inputs' in node:
                slot_to_link = node_input_updates[node_id_str]
                inputs = node.get('inputs', [])

                # Update the input at each slot to reference the external link ID
                for i, input_info in enumerate(inputs):
                    # The slot corresponds to the index in the inputs list
                    if i in slot_to_link:
                        input_info['link'] = slot_to_link[i]

        # Build link map for quick lookup
        # link_id -> (source_node_id, source_slot, target_node_id, target_slot, type)
        link_map = {}
        # Also track which nodes are connected to others (have outputs that go somewhere)
        nodes_with_connected_outputs = set()
        
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
                # Track that this source node has connected outputs
                nodes_with_connected_outputs.add(source_id)
        
        # First pass: identify PrimitiveNodes, GetNode/SetNode pairs, and their values
        # Also identify nodes that should be excluded from API format
        primitive_values = {}
        nodes_to_exclude = set()
        bypassed_nodes = set()  # Track bypassed/disabled nodes
        
        # Track GetNode/SetNode variable mappings
        # SetNode: variable_name -> (source_node_id, source_slot) from its input link
        # GetNode: node_id -> variable_name
        set_node_sources = {}  # variable_name -> (source_node_id, source_slot)
        get_node_vars = {}  # node_id (str) -> variable_name
        
        # First, build the node lookup map for quick access
        node_by_id = {}
        for node in workflow_nodes:
            node_by_id[str(node.get('id'))] = node
        
        for node in workflow_nodes:
            node_id = node.get('id')
            node_type = node.get('type')
            node_mode = node.get('mode', 0)
            
            # Track bypassed/disabled nodes
            # Store as string to match link_data['source_id'] type
            if node_mode == 4:
                bypassed_nodes.add(str(node_id))
                logger.debug(f"Tracking bypassed node {node_id} ({node_type})")
            
            # Track primitive nodes
            if node_type == 'PrimitiveNode':
                node_id_str = str(node_id)
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    primitive_values[node_id_str] = widget_values[0]
            
            # Track SetNode: maps variable name to its input source
            elif node_type == 'SetNode':
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    var_name = widget_values[0]
                    # Find the input link to this SetNode
                    node_inputs = node.get('inputs', [])
                    if node_inputs:
                        for input_info in node_inputs:
                            input_link = input_info.get('link')
                            if input_link is not None and input_link in link_map:
                                link_data = link_map[input_link]
                                set_node_sources[var_name] = (link_data['source_id'], link_data['source_slot'])
                                logger.debug(f"SetNode '{var_name}' receives from node {link_data['source_id']} slot {link_data['source_slot']}")
                                break
            
            # Track GetNode: maps node_id to variable name
            elif node_type == 'GetNode':
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    var_name = widget_values[0]
                    get_node_vars[str(node_id)] = var_name
                    logger.debug(f"GetNode {node_id} provides variable '{var_name}'")
            
            # Check if this node should be excluded from API format
            # Exclude nodes that have no connected outputs (UI-only nodes)
            outputs = node.get('outputs', [])
            has_connected_output = False
            for output in outputs:
                output_links = output.get('links', [])
                if output_links and len(output_links) > 0:
                    has_connected_output = True
                    break
            
            # Check if this is a special UI-only node type that should be excluded
            # LoadImageOutput is a special case - it's for loading from the output folder
            # which is a UI convenience that shouldn't be in the API format
            if node_type == 'LoadImageOutput':
                nodes_to_exclude.add(str(node_id))
                logger.debug(f"Marking node {node_id} ({node_type}) for exclusion - UI-only node type")
            # If node has no outputs or no connected outputs, it should be excluded
            # unless it's an OUTPUT_NODE (like SaveImage, PreviewImage)
            elif not outputs or not has_connected_output:
                # Check if this is an OUTPUT_NODE that should be kept
                node_class = nodes.NODE_CLASS_MAPPINGS.get(node_type) if hasattr(nodes, 'NODE_CLASS_MAPPINGS') else None
                is_output_node = node_class and hasattr(node_class, 'OUTPUT_NODE') and node_class.OUTPUT_NODE
                
                # Fallback: if node class isn't available but node has connected inputs,
                # it's likely an output node (e.g., SaveVideo, SaveImage) that should be kept
                has_connected_input = False
                if not is_output_node and not node_class:
                    node_inputs = node.get('inputs', [])
                    for input_info in node_inputs:
                        if input_info.get('link') is not None:
                            has_connected_input = True
                            break
                
                if not is_output_node and not has_connected_input:
                    nodes_to_exclude.add(str(node_id))
                    logger.debug(f"Marking node {node_id} ({node_type}) for exclusion - no connected outputs")
                else:
                    logger.debug(f"Keeping node {node_id} ({node_type}) - OUTPUT_NODE=True or has connected inputs despite no connected outputs")
        
        # Helper function to trace through GetNode/SetNode pairs
        def trace_through_get_set_nodes(source_node_id, source_slot, visited=None):
            """
            Trace through GetNode/SetNode pairs to find the actual source.
            GetNode outputs the value that was set by a corresponding SetNode.
            Returns (actual_source_id, actual_source_slot) tuple.
            """
            if visited is None:
                visited = set()
            
            source_node_id_str = str(source_node_id)
            
            # Avoid infinite loops
            if source_node_id_str in visited:
                return (source_node_id, source_slot)
            visited.add(source_node_id_str)
            
            # Check if source is a GetNode
            if source_node_id_str in get_node_vars:
                var_name = get_node_vars[source_node_id_str]
                # Find the SetNode that provides this variable
                if var_name in set_node_sources:
                    actual_source_id, actual_source_slot = set_node_sources[var_name]
                    logger.debug(f"Tracing GetNode {source_node_id} (var '{var_name}') -> actual source {actual_source_id} slot {actual_source_slot}")
                    # Recursively trace in case the SetNode's source is also a GetNode
                    return trace_through_get_set_nodes(actual_source_id, actual_source_slot, visited)
                else:
                    logger.warning(f"GetNode {source_node_id} references variable '{var_name}' but no SetNode found for it")
            
            # Not a GetNode or couldn't trace, return as-is
            return (source_node_id, source_slot)
        
        # Helper function to trace through bypassed nodes
        def trace_through_bypassed(source_node_id, source_slot, visited=None):
            """
            Trace through bypassed nodes to find the actual source.
            Returns (actual_source_id, actual_source_slot) tuple.

            Now handles ALL input types (widgets, strings, etc), not just image/latent.
            This fixes kjnodes (WidgetToString) and other widget-based bypassed nodes.
            """
            if visited is None:
                visited = set()
            
            # Avoid infinite loops
            if source_node_id in visited:
                return (source_node_id, source_slot)
            visited.add(source_node_id)
            
            # If source is not bypassed, return it as-is
            if source_node_id not in bypassed_nodes:
                return (source_node_id, source_slot)
            
            # Find the input to this bypassed node
            for node in workflow_nodes:
                # Convert to string for comparison (source_node_id is string, node.get('id') is int)
                if str(node.get('id')) == str(source_node_id):
                    node_type = node.get('type', 'unknown')
                    # Look for the input that should be passed through
                    node_inputs = node.get('inputs', [])
                    node_outputs = node.get('outputs', [])
                    
                    # Get the output type at source_slot to match with the right input
                    output_type = None
                    if node_outputs and source_slot < len(node_outputs):
                        output_type = node_outputs[source_slot].get('type')

                    if node_inputs:
                        # When bypassed, the node passes through input -> output
                        # Try to find an input that matches the output type
                        linked_input = None
                        fallback_linked_input = None
                        
                        for idx, input_info in enumerate(node_inputs):
                            input_link = input_info.get('link')
                            input_type = input_info.get('type')

                            if input_link is not None and input_link in link_map:
                                # Keep track of first linked input as fallback
                                if fallback_linked_input is None:
                                    fallback_linked_input = input_link
                                
                                # Prefer input that matches output type
                                if output_type and input_type == output_type:
                                    linked_input = input_link
                                    break
                        
                        # Use matched input if found, otherwise fallback to first linked input
                        if linked_input is None:
                            linked_input = fallback_linked_input

                        if linked_input is not None:
                            link_data = link_map[linked_input]
                            upstream_source_id = link_data['source_id']
                            upstream_source_slot = link_data['source_slot']
                            
                            # First trace through GetNode/SetNode pairs
                            upstream_source_id, upstream_source_slot = trace_through_get_set_nodes(
                                upstream_source_id,
                                upstream_source_slot
                            )
                            
                            # Recursively trace through this source (for more bypassed nodes)
                            return trace_through_bypassed(
                                upstream_source_id,
                                upstream_source_slot,
                                visited
                            )
                    break
            
            # If we couldn't trace further, return original
            return (source_node_id, source_slot)
        
        # Build API format prompt
        api_prompt = {}
        
        for node in workflow_nodes:
            node_id = str(node.get('id'))
            node_type = node.get('type')
            
            if not node_type:
                continue
                
            # Skip muted and bypassed/disabled nodes
            node_mode = node.get('mode', 0)
            if node_mode == 2:  # Mode 2 is muted
                logger.debug(f"Skipping muted node {node_id} ({node_type})")
                continue
            elif node_mode == 4:  # Mode 4 is bypassed/disabled
                logger.debug(f"Skipping bypassed/disabled node {node_id} ({node_type})")
                continue
            
            # Skip non-executable nodes
            # These include UI-only nodes, routing nodes, and nodes with no connected outputs
            if node_type in ['Note', 'PrimitiveNode', 'GetNode', 'SetNode']:
                logger.debug(f"Skipping {node_type} node {node_id}")
                continue
            
            # Skip nodes that were identified as having no connected outputs
            # Use node_id (string) instead of node.get('id') (int) to match nodes_to_exclude type
            if node_id in nodes_to_exclude:
                logger.debug(f"Skipping {node_type} node {node_id} - no connected outputs")
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
                        source_node_id = link_data['source_id']
                        source_slot = link_data['source_slot']

                        # First, trace through GetNode/SetNode pairs to find actual source
                        # This is needed because GetNode/SetNode are routing nodes that aren't in the API output
                        actual_source_id, actual_source_slot = trace_through_get_set_nodes(
                            source_node_id,
                            source_slot
                        )
                        
                        # If source is bypassed, try to trace through to find the actual upstream source
                        # This handles passthrough nodes (e.g., ModelSamplingAuraFlow) where bypassing
                        # should just pass the input through to the output
                        if str(actual_source_id) in bypassed_nodes:
                            actual_source_id, actual_source_slot = trace_through_bypassed(
                                actual_source_id,
                                actual_source_slot
                            )
                            # If we couldn't find a valid upstream source (still bypassed or no linked input),
                            # skip this connection and let the target node fall back to widget values
                            if str(actual_source_id) in bypassed_nodes:
                                logger.debug(f"Could not trace through bypassed node {source_node_id} for input {input_name}, falling back to widget value")
                                continue
                        
                        # After tracing through bypassed nodes, also trace through any GetNode/SetNode pairs again
                        # (in case the bypassed node's source was a GetNode)
                        actual_source_id, actual_source_slot = trace_through_get_set_nodes(
                            actual_source_id,
                            actual_source_slot
                        )
                        
                        # Resolve subgraph outputs - if the source is a subgraph node, find the actual internal node
                        # This is needed because GetNode/SetNode might point to a subgraph's output
                        actual_source_id, actual_source_slot = resolve_subgraph_output(
                            str(actual_source_id),
                            actual_source_slot
                        )
                        
                        source_node_id_str = str(actual_source_id)
                        
                        # Check if the source is a PrimitiveNode or excluded node
                        if source_node_id_str in primitive_values:
                            # This is a resolved primitive value - treat as widget for ordering
                            primitive_inputs[input_name] = primitive_values[source_node_id_str]
                        elif actual_source_id in nodes_to_exclude:
                            # Source node is excluded (not an executable node), skip this input connection
                            logger.debug(f"Skipping input {input_name} from excluded node {source_node_id_str}")
                        elif actual_source_id in bypassed_nodes:
                            # If we still ended up with a bypassed node after tracing, it means
                            # the trace failed (couldn't find a non-bypassed source). Skip this connection.
                            logger.warning(f"Could not resolve bypassed node {source_node_id} for input {input_name}, skipping connection")
                        else:
                            # Keep as link with the actual source (after bypassing any disabled nodes)
                            if actual_source_id != source_node_id:
                                logger.info(f"Bypassed disabled node {source_node_id}, connecting {input_name} to {actual_source_id} (slot {actual_source_slot}) instead")
                            # Use actual_source_id (the traced node) not source_node_id (the bypassed node)
                            link_inputs[input_name] = [str(actual_source_id), actual_source_slot]

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
                    # First check if widget values contain dictionaries with self-describing names
                    has_dict_widgets = any(isinstance(v, dict) for v in widget_values)
                    
                    if has_dict_widgets:
                        # Handle widget values that are dictionaries
                        # These often self-describe their input names
                        WorkflowConverter._process_dict_widget_values(widget_values, widget_inputs, link_inputs)
                    else:
                        # Regular widget values - need to map to widget names
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
            
            # Get default values for inputs that aren't set via widgets or links
            # This is important for required inputs with defaults that aren't in widget_values
            default_inputs = WorkflowConverter._get_default_inputs(node_type, widget_inputs, primitive_inputs, link_inputs)
            
            # Build inputs in the correct order
            # The official "Save (API)" format follows this pattern:
            # ALL widget values first (in node class order), then ALL link inputs (in node class order)
            # Note: Resolved primitive values are treated as widgets for ordering
            if ordered_inputs:
                # First pass: add all widget values in order (including resolved primitives and defaults)
                for input_name in ordered_inputs:
                    if input_name in widget_inputs:
                        api_node['inputs'][input_name] = widget_inputs[input_name]
                    elif input_name in primitive_inputs:
                        api_node['inputs'][input_name] = primitive_inputs[input_name]
                    elif input_name in default_inputs:
                        api_node['inputs'][input_name] = default_inputs[input_name]
                
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
                for key, value in default_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in link_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
            else:
                # Fallback when we don't have the node class: add all inputs in order they appear
                # First add ALL widget inputs, primitives, and defaults, then ALL link inputs
                for key, value in widget_inputs.items():
                    api_node['inputs'][key] = value
                for key, value in primitive_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in default_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in link_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
            
            api_prompt[node_id] = api_node
        
        return api_prompt
    
    @staticmethod
    def _process_dict_widget_values(widget_values: List[Any], widget_inputs: Dict[str, Any], link_inputs: Dict[str, Any]) -> None:
        """
        Process widget values that contain dictionaries.
        These are self-describing widgets that contain their configuration as dicts.
        """
        lora_counter = 0
        
        for value in widget_values:
            if isinstance(value, dict):
                if not value:
                    # Empty dict - skip
                    continue
                elif 'type' in value:
                    # Widget with a type field - use the type as the input name
                    widget_name = value.get('type')
                    if widget_name and widget_name not in link_inputs:
                        widget_inputs[widget_name] = value
                elif 'lora' in value:
                    # This is a lora configuration entry
                    lora_counter += 1
                    widget_name = f'lora_{lora_counter}'
                    if widget_name not in link_inputs:
                        # Remove 'strengthTwo' if it's None (not used in API format)
                        clean_value = {k: v for k, v in value.items() if k != 'strengthTwo' or v is not None}
                        widget_inputs[widget_name] = clean_value
                else:
                    # Unknown dict structure - include it as-is with a generic name
                    # This ensures we don't lose data even for unknown structures
                    logger.debug(f"Unknown dict widget value structure: {value}")
            elif isinstance(value, str):
                # String values at the end often represent buttons or special controls
                # The "➕ Add Lora" button is a common example
                if value == '':
                    # Empty string often represents the "Add" button
                    widget_inputs['➕ Add Lora'] = value
            # Skip None values and other types that don't map to widgets
    
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
    def _get_dynamic_combo_sub_inputs(input_name: str, input_spec: Any, widget_values: List[Any], current_idx: int) -> List[str]:
        """
        Get sub-input names for a COMFY_DYNAMICCOMBO_V3 input based on the selected option.
        Returns list of sub-input names with dot notation (e.g., ['resize_type.width', 'resize_type.height']).
        """
        if not isinstance(input_spec, (list, tuple)) or len(input_spec) < 2:
            return []
        
        input_type = input_spec[0]
        if not isinstance(input_type, str) or not input_type.startswith('COMFY_') or 'COMBO' not in input_type:
            return []
        
        spec_options = input_spec[1] if len(input_spec) > 1 else {}
        if not isinstance(spec_options, dict):
            return []
        
        options = spec_options.get('options', [])
        if not options or current_idx >= len(widget_values):
            return []
        
        # Get the selected option value
        selected_value = widget_values[current_idx]
        
        # Find the matching option and its sub-inputs
        for option in options:
            if isinstance(option, dict) and option.get('key') == selected_value:
                sub_inputs = option.get('inputs', {})
                sub_input_names = []
                for section in ['required', 'optional']:
                    if section in sub_inputs:
                        for sub_name in sub_inputs[section].keys():
                            sub_input_names.append(f"{input_name}.{sub_name}")
                return sub_input_names
        
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
        
        widget_values = node.get('widgets_values', [])
        if isinstance(widget_values, dict):
            # Dict format - keys are widget names
            widget_values = []  # Can't use for index-based sub-input lookup
        
        # Try to get from cached node info first
        node_info = get_node_info_for_type(node_type)
        if node_info and 'input' in node_info:
            try:
                input_def = node_info['input']
                widget_names = []
                widget_idx = 0

                # Process required inputs first, then optional
                for section in ['required', 'optional']:
                    if section in input_def:
                        for input_name, input_spec in input_def[section].items():
                            # Check if this is a widget input (not a node connection)
                            if isinstance(input_spec, (list, tuple)) and len(input_spec) >= 1:
                                input_type = input_spec[0]
                                is_widget = False
                                is_dynamic_combo = False
                                
                                # Check if it's a widget type
                                if isinstance(input_type, list):
                                    # This is a combo box widget (list of choices)
                                    is_widget = True
                                elif input_type in ['INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO']:
                                    # Basic widget types (COMBO is also a widget type, not a connection)
                                    is_widget = True
                                elif isinstance(input_type, str) and input_type.startswith('COMFY_') and 'COMBO' in input_type:
                                    # ComfyUI V3 dynamic combo types (e.g., COMFY_DYNAMICCOMBO_V3)
                                    is_widget = True
                                    is_dynamic_combo = True
                                elif isinstance(input_type, str) and not input_type.isupper():
                                    # Custom widget types (lowercase)
                                    is_widget = True
                                
                                if is_widget:
                                    widget_names.append(input_name)
                                    
                                    # Handle dynamic combo sub-inputs
                                    if is_dynamic_combo and widget_values:
                                        sub_inputs = WorkflowConverter._get_dynamic_combo_sub_inputs(
                                            input_name, input_spec, widget_values, widget_idx
                                        )
                                        widget_names.extend(sub_inputs)
                                        widget_idx += 1 + len(sub_inputs)
                                    else:
                                        widget_idx += 1

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
                widget_idx = 0

                # Process required inputs first, then optional
                for section in ['required', 'optional']:
                    if section in input_types:
                        for input_name, input_spec in input_types[section].items():
                            # Check if this is a widget input (not a node connection)
                            if isinstance(input_spec, tuple) and len(input_spec) >= 1:
                                input_type = input_spec[0]
                                is_widget = False
                                is_dynamic_combo = False
                                
                                # Check if it's a widget type
                                if isinstance(input_type, list):
                                    # This is a combo box widget (list of choices)
                                    is_widget = True
                                elif input_type in ['INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO']:
                                    # Basic widget types (COMBO is also a widget type, not a connection)
                                    is_widget = True
                                elif isinstance(input_type, str) and input_type.startswith('COMFY_') and 'COMBO' in input_type:
                                    # ComfyUI V3 dynamic combo types (e.g., COMFY_DYNAMICCOMBO_V3)
                                    is_widget = True
                                    is_dynamic_combo = True
                                elif isinstance(input_type, str) and not input_type.isupper():
                                    # Custom widget types (lowercase)
                                    is_widget = True
                                
                                if is_widget:
                                    widget_names.append(input_name)
                                    
                                    # Handle dynamic combo sub-inputs
                                    if is_dynamic_combo and widget_values:
                                        sub_inputs = WorkflowConverter._get_dynamic_combo_sub_inputs(
                                            input_name, input_spec, widget_values, widget_idx
                                        )
                                        widget_names.extend(sub_inputs)
                                        widget_idx += 1 + len(sub_inputs)
                                    else:
                                        widget_idx += 1

                if widget_names:
                    return widget_names
            except Exception as e:
                logger.debug(f"Could not get widget mappings from node class for {node_type}: {e}")
        
        # Fallback: Try to infer widget mappings from the workflow and widget values
        widget_values = node.get('widgets_values', [])
        if not isinstance(widget_values, list) or len(widget_values) == 0:
            return []

        # Try to get widget names from ue_properties.widget_ue_connectable
        # This property lists all widgets that can be converted to connectable inputs
        properties = node.get('properties', {})
        ue_properties = properties.get('ue_properties', {})
        widget_ue_connectable = ue_properties.get('widget_ue_connectable', {})

        if widget_ue_connectable and isinstance(widget_ue_connectable, dict):
            # Get the keys which are the widget names
            # These are in dictionary order, which should match the widget_values order
            widget_names = list(widget_ue_connectable.keys())
            if widget_names and len(widget_names) >= len(widget_values):
                return widget_names[:len(widget_values)]

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
    
    @staticmethod
    def _get_default_inputs(node_type: str, widget_inputs: Dict[str, Any], primitive_inputs: Dict[str, Any], link_inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get default values for required/optional inputs that aren't already set.
        
        This is important for nodes where required inputs have default values defined
        in the node class, but the workflow doesn't explicitly set them (using defaults).
        The official "Save (API)" format includes these default values.
        
        Args:
            node_type: The node class type
            widget_inputs: Already processed widget inputs
            primitive_inputs: Already processed primitive inputs  
            link_inputs: Already processed link inputs
            
        Returns:
            Dict of input names to their default values for inputs that aren't already set
        """
        default_inputs = {}
        
        # Get the node class to access INPUT_TYPES
        if not hasattr(nodes, 'NODE_CLASS_MAPPINGS') or node_type not in nodes.NODE_CLASS_MAPPINGS:
            return default_inputs
        
        try:
            node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
            input_types = node_class.INPUT_TYPES()
            
            # Check both required and optional inputs
            for section in ['required', 'optional']:
                if section not in input_types:
                    continue
                    
                for input_name, input_spec in input_types[section].items():
                    # Skip if already set
                    if input_name in widget_inputs or input_name in primitive_inputs or input_name in link_inputs:
                        continue
                    
                    # Check if this input has a default value
                    if isinstance(input_spec, (list, tuple)) and len(input_spec) >= 1:
                        input_type = input_spec[0]
                        spec_options = input_spec[1] if len(input_spec) >= 2 else {}
                        
                        # Check for explicit default in options dict
                        if isinstance(spec_options, dict) and 'default' in spec_options:
                            default_inputs[input_name] = spec_options['default']
                            logger.debug(f"Adding default value for {node_type}.{input_name}: {spec_options['default']}")
                        # For combo boxes (list type), use first item as implicit default
                        elif isinstance(input_type, list) and len(input_type) > 0:
                            default_inputs[input_name] = input_type[0]
                            logger.debug(f"Adding implicit combo default for {node_type}.{input_name}: {input_type[0]}")
                        # For COMBO type with options dict, use first option as implicit default
                        elif input_type == 'COMBO' and isinstance(spec_options, dict) and 'options' in spec_options:
                            options = spec_options['options']
                            if isinstance(options, list) and len(options) > 0:
                                default_inputs[input_name] = options[0]
                                logger.debug(f"Adding implicit COMBO default for {node_type}.{input_name}: {options[0]}")
        except Exception as e:
            logger.debug(f"Could not get default inputs for {node_type}: {e}")
        
        return default_inputs