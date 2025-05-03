
import argparse
import csv
import json
import os
import sys
import xml.dom.minidom
from datetime import datetime
from typing import Dict, List, Union, Optional, Any
import subprocess
import io # Needed for StringIO

# Attempt to import dependencies, provide guidance if missing
try:
    # NOTE: These imports work when the script is called via subprocess,
    # assuming the calling environment has them installed.
    import Evtx.Evtx as evtx
    import Evtx.Views as e_views
except ImportError:
    # This message will appear in stderr if the script fails early
    print(json.dumps({"error": "Dependency 'python-evtx' not found in the calling environment. Install using: pip install python-evtx"}), file=sys.stderr)
    sys.exit(1)

try:
    import win32evtlog
    import win32evtlogutil
    import win32con
    import winerror
    HAS_WIN32 = True
except ImportError:
    # Don't exit, just note that rendering might be limited
    # This message will appear in stderr
    print(json.dumps({"warning": "Dependency 'pywin32' not found. Message rendering may be limited. Install using: pip install pywin32"}), file=sys.stderr)
    HAS_WIN32 = False

class EvtxParser:
    """Class for parsing and extracting data from EVTX files"""

    def __init__(self, file_path: str, debug: bool = False):
        """
        Initialize the EVTX parser

        Args:
            file_path: Path to the EVTX file
            debug: Enable debug printing to stderr
        """
        self.file_path = file_path
        self.debug = debug
        if not os.path.exists(file_path):
            # Use JSON format for errors sent to stderr
            print(json.dumps({"error": f"EVTX file not found: {file_path}"}), file=sys.stderr)
            raise FileNotFoundError(f"EVTX file not found: {file_path}") # Also raise for the caller

        self.message_cache = {} # Cache for rendered messages

    def _print_debug(self, message: str):
        """Prints a message to stderr if debug is enabled."""
        if self.debug:
            # Prefix debug messages clearly
            print(f"[DEBUG] {message}", file=sys.stderr)

    def get_record_count(self) -> int:
        """Return the total number of records in the EVTX file"""
        count = 0
        try:
            with evtx.Evtx(self.file_path) as evtx_file:
                # Use a generator expression for efficiency if file is large
                count = sum(1 for _ in evtx_file.records())
        except Exception as e:
            print(json.dumps({"error": f"Failed to count records: {e}"}), file=sys.stderr)
            # Decide if we should exit or return 0
            return 0 # Or raise e
        return count

    def get_records_as_text(self, limit: Optional[int] = None, render_messages: bool = True) -> str:
        """
        Extract records from the EVTX file and format as a single text block.
        This is the primary method used by the Streamlit app.

        Args:
            limit: Maximum number of records to extract (None for all)
            render_messages: If True, attempt to retrieve rendered message text

        Returns:
            A string containing formatted event data.
        """
        # Use StringIO for efficient string building
        output = io.StringIO()
        count = 0

        self._print_debug(f"Starting text extraction. Limit: {limit}, Render Messages: {render_messages}, Has Win32: {HAS_WIN32}")

        try:
            with evtx.Evtx(self.file_path) as evtx_file:
                for record in evtx_file.records():
                    if limit is not None and count >= limit:
                        self._print_debug(f"Reached record limit ({limit}). Stopping.")
                        break

                    try:
                        xml_str = record.xml()
                        event_data = self._parse_xml_to_dict(xml_str)
                        event_data['record_num'] = record.record_num() # Add record number

                        if 'error' in event_data: # Check if XML parsing failed
                             self._print_debug(f"Skipping record {record.record_num()} due to XML parsing error: {event_data['details']}")
                             output.write(f"--- Event Record {record.record_num()} --- [XML Parsing Error: {event_data['details']}] ---\n\n")
                             continue

                        # --- Message Rendering ---
                        if render_messages:
                            if HAS_WIN32:
                                self._add_rendered_message(event_data)
                                # Debug log the rendered message or lack thereof
                                self._print_debug(f"Record {event_data['record_num']} RenderedMsg: {event_data.get('RenderedMessage', '[Not Found]')[:100]}...")
                            else:
                                # Add placeholder if pywin32 not available
                                event_data['RenderedMessage'] = "[Message rendering unavailable: pywin32 not installed]"
                                if count == 0: # Only print warning once
                                     self._print_debug("pywin32 not found, message rendering disabled.")


                        # --- Format the record into a readable string ---
                        output.write(f"--- Event Record {event_data['record_num']} ---\n")

                        # Prioritize rendered message if available and not just a placeholder/error
                        rendered_msg = event_data.get('RenderedMessage')
                        if rendered_msg and not rendered_msg.startswith("[Message rendering"):
                            output.write(f"Message: {rendered_msg}\n")

                        # Include key system info if available
                        system_info = event_data.get('System', {})
                        provider_info = system_info.get('Provider', {})
                        eventid_info = system_info.get('EventID', {})
                        time_info = system_info.get('TimeCreated', {})

                        output.write(f"  Provider: {provider_info.get('Name', 'N/A')}\n")
                        output.write(f"  Event ID: {eventid_info.get('Value', 'N/A')}\n")
                        level_map = {"0": "LogAlways", "1": "Critical", "2": "Error", "3": "Warning", "4": "Information", "5": "Verbose"}
                        output.write(f"  Level: {system_info.get('Level', 'N/A')} ({level_map.get(str(system_info.get('Level')), 'Unknown')})\n")
                        output.write(f"  TimeCreated: {time_info.get('SystemTime', 'N/A')}\n")
                        output.write(f"  Computer: {system_info.get('Computer', 'N/A')}\n")
                        output.write(f"  Channel: {system_info.get('Channel', 'N/A')}\n")
                        if system_info.get('Security') and system_info['Security'].get('UserID'):
                            output.write(f"  Security UserID: {system_info['Security']['UserID']}\n")

                        # Include EventData if present
                        event_data_content = event_data.get('EventData')
                        if event_data_content and isinstance(event_data_content, dict):
                            output.write("  Event Data:\n")
                            for key, value in event_data_content.items():
                                # Format multi-line values nicely
                                formatted_value = str(value).replace('\n', '\n      ') if isinstance(value, str) and '\n' in value else value
                                output.write(f"    {key}: {formatted_value}\n")
                        elif event_data.get('UserData'): # Show UserData if EventData is absent
                             output.write("  User Data:\n")
                             # Basic representation of UserData (can be complex XML)
                             output.write(f"    {json.dumps(event_data['UserData'], indent=6)}\n") # Pretty print JSON

                        output.write("-" * (len(f"--- Event Record {event_data['record_num']} ---") + 1)) # Separator matching title length
                        output.write("\n\n") # Double newline for readability between records

                        count += 1
                    except Exception as e:
                        error_msg = f"Error processing record {record.record_num()}: {e}"
                        # Output the error directly into the text stream for this record
                        output.write(f"--- Event Record {record.record_num()} --- [Processing Error: {error_msg}] ---\n\n")
                        print(json.dumps({"error": error_msg}), file=sys.stderr)
                        self._print_debug(error_msg)
                        if self.debug:
                            import traceback
                            traceback.print_exc(file=sys.stderr)
                        continue # Skip to next record on error

        except evtx.EvtxError as e:
             error_msg = f"Error reading EVTX file structure '{self.file_path}': {e}"
             print(json.dumps({"error": error_msg}), file=sys.stderr)
             self._print_debug(error_msg)
             # Append error to output and return what we have
             output.write(f"\n*** ERROR READING EVTX FILE: {error_msg} ***\n")
             return output.getvalue()
        except Exception as e:
            error_msg = f"Unexpected error reading EVTX file '{self.file_path}': {e}"
            print(json.dumps({"error": error_msg}), file=sys.stderr)
            self._print_debug(error_msg)
            if self.debug:
                import traceback
                traceback.print_exc(file=sys.stderr)
            # Append error to output and return what we have
            output.write(f"\n*** UNEXPECTED ERROR READING EVTX FILE: {error_msg} ***\n")
            return output.getvalue() # Return what we have so far

        self._print_debug(f"Finished text extraction. Processed {count} records.")
        return output.getvalue()


    def _powershell_render_message_by_event_id(self, event_id: Union[int, str]) -> Optional[str]:
        """Fallback using PowerShell (requires PowerShell and execution policy)"""
        if not sys.platform.startswith('win'):
            self._print_debug("PowerShell fallback skipped: Not on Windows.")
            return None
        try:
            # Ensure file path is properly quoted for PowerShell
            ps_safe_path = self.file_path.replace("'", "''")
            # Correct PowerShell command to filter by ID and get the message
            ps_cmd = (
                f"$ErrorActionPreference = 'Stop'; " # Stop on errors
                f"Get-WinEvent -Path '{ps_safe_path}' -FilterHashtable @{{Id={event_id}}} -MaxEvents 1 | "
                f"Select-Object -ExpandProperty Message -ErrorAction SilentlyContinue" # Select only message, ignore if none
            )
            self._print_debug(f"Running PowerShell fallback: {ps_cmd}")
            # Use a timeout to prevent hanging
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                encoding='utf-8', # Specify encoding
                timeout=15 # Add a timeout (e.g., 15 seconds)
            )
            self._print_debug(f"PowerShell exit code: {result.returncode}")
            if result.returncode == 0 and result.stdout and result.stdout.strip():
                message_text = result.stdout.strip()
                self._print_debug(f"PowerShell fallback success: {message_text[:100]}...")
                return message_text
            elif result.stderr:
                self._print_debug(f"PowerShell fallback stderr: {result.stderr.strip()}")

        except FileNotFoundError:
             self._print_debug("PowerShell command not found. Is PowerShell installed and in PATH?")
        except subprocess.TimeoutExpired:
            self._print_debug("PowerShell command timed out.")
        except Exception as e:
            self._print_debug(f"PowerShell fallback error: {e}")
        return None


    def _add_rendered_message(self, event_data: Dict[str, Any]) -> None:
        """
        Add rendered message to event data using Windows message resources or PowerShell fallback.
        Requires pywin32 to be installed and running on Windows.

        Args:
            event_data: Dictionary containing the event data
        """
        if not HAS_WIN32:
            # This case is handled in get_records_as_text, but double-check
            event_data["RenderedMessage"] = "[Message rendering unavailable: pywin32 not installed]"
            return

        try:
            # Safely access potentially missing keys
            provider_name = event_data.get("System", {}).get("Provider", {}).get("Name")
            event_id_val = event_data.get("System", {}).get("EventID", {}).get("Value")
            qualifiers_str = event_data.get("System", {}).get("EventID", {}).get("Qualifiers", "0") # Default qualifiers to "0"

            if not provider_name or not event_id_val:
                self._print_debug("Missing Provider Name or EventID for message rendering.")
                event_data["RenderedMessage"] = "[Message rendering failed: Missing key info]"
                return

            # Attempt to convert IDs safely
            try:
                event_id = int(event_id_val)
                qualifiers = int(qualifiers_str) if qualifiers_str else 0
            except (ValueError, TypeError):
                 self._print_debug(f"Invalid EventID or Qualifiers format: ID='{event_id_val}', Qualifiers='{qualifiers_str}'")
                 event_data["RenderedMessage"] = "[Message rendering failed: Invalid ID format]"
                 return

            # Event ID in pywin32 often includes qualifiers shifted
            event_id_with_qualifiers = event_id | (qualifiers << 16)
            cache_key = f"{provider_name}:{event_id_with_qualifiers}" # Use qualified ID for cache

            message = None
            if cache_key in self.message_cache:
                message = self.message_cache[cache_key]
                if message: # Only log hit if message was actually found previously
                     self._print_debug(f"Message cache hit for {cache_key}")
            else:
                 # Try rendering using different potential source names and IDs
                 # Often the 'EventSourceName' is needed if 'Name' is a GUID
                potential_sources = [
                    provider_name,
                    event_data.get("System", {}).get("Provider", {}).get("EventSourceName"),
                ]
                potential_ids = [
                     event_id_with_qualifiers, # Try with qualifiers first
                     event_id # Fallback to plain event ID
                ]
                found_message = False
                for source in filter(None, potential_sources): # Filter out None/empty source names
                    for id_to_try in potential_ids:
                        try:
                            self._print_debug(f"Attempting FormatMessage with Source='{source}', ID={id_to_try}")
                            # Ensure parameters are passed correctly if available
                            insert_tuple = None
                            event_data_content = event_data.get("EventData")
                            if event_data_content and isinstance(event_data_content, dict):
                                 # Extract parameters in expected order (Data_0, Data_1 or Data names)
                                 # This part is tricky and might need refinement based on common event formats
                                 params_list = []
                                 idx = 0
                                 while f"Data_{idx}" in event_data_content or f"Data{idx}" in event_data_content or str(idx) in event_data_content:
                                     val = event_data_content.get(f"Data_{idx}", event_data_content.get(f"Data{idx}", event_data_content.get(str(idx))))
                                     params_list.append(str(val) if val is not None else "") # Convert all params to string for FormatMessage
                                     idx += 1
                                 if params_list:
                                     insert_tuple = tuple(params_list)
                                     self._print_debug(f"  Using insertion parameters: {insert_tuple}")


                            # Call FormatMessage with insertion parameters if available
                            message = win32evtlogutil.FormatMessage(source, id_to_try, insertTuple=insert_tuple)

                            if message and message.strip(): # Found a non-empty message, stop trying
                                self._print_debug(f"FormatMessage successful for Source='{source}', ID={id_to_try}")
                                found_message = True
                                break
                            else:
                                 self._print_debug(f"FormatMessage returned empty for Source='{source}', ID={id_to_try}")

                        except Exception as fmt_e:
                            # pywintypes.error: (15100, 'FormatMessage', 'The resource loader failed to find MUI file.') is common
                            # Error code 1815 (ERROR_MR_MID_NOT_FOUND) is also common ("message resource present but message not found")
                            error_code = fmt_e.args[0] if hasattr(fmt_e, 'args') and isinstance(fmt_e.args, tuple) and len(fmt_e.args) > 0 else 'Unknown'
                            if error_code != 15100 and error_code != winerror.ERROR_MR_MID_NOT_FOUND: # Suppress common "not found" errors unless debugging
                                self._print_debug(f"FormatMessage error for Source='{source}', ID={id_to_try}: {fmt_e} (Code: {error_code})")
                            else:
                                 self._print_debug(f"FormatMessage known error (resource/message not found) for Source='{source}', ID={id_to_try} (Code: {error_code})")
                    if found_message:
                        break # Stop checking sources if message found

                self.message_cache[cache_key] = message # Cache result (even if None or empty)

            # Parameter insertion logic (if message template was found but FormatMessage didn't handle inserts)
            # This is a fallback, FormatMessage *should* handle inserts if possible
            if message and isinstance(message, str) and '%' in message and event_data.get("EventData") and not found_message: # Only if FormatMessage didn't populate inserts
                try:
                    # This simplistic replacement is less robust than FormatMessage's internal logic
                    event_params_dict = event_data.get("EventData", {})
                    import re

                    def replace_param(match):
                        try:
                            placeholder = match.group(0) # e.g., %1, %2
                            # Try matching named params first if they exist (e.g., %{ParamName}) - less common
                            # named_match = re.match(r"%\{(.+?)\}", placeholder)
                            # if named_match and named_match.group(1) in event_params_dict:
                            #     return str(event_params_dict[named_match.group(1)])

                            # Try numbered params (%1, %2, ...)
                            num_match = re.match(r"%([1-9]\d*)", placeholder)
                            if num_match:
                                idx = int(num_match.group(1)) - 1 # %1 -> index 0
                                # Try mapping to Data_idx or Dataidx or idx keys
                                key_options = [f"Data_{idx}", f"Data{idx}", str(idx)]
                                for key in key_options:
                                     if key in event_params_dict:
                                         return str(event_params_dict[key])
                                return match.group(0) # Placeholder index not found in data
                            return match.group(0) # Not a recognized placeholder format
                        except (IndexError, ValueError, KeyError):
                             return match.group(0) # Error during replacement, keep original

                    # Regex to find % followed by digits (or maybe %{name}), avoiding %%
                    # This regex focuses on %1, %2 etc.
                    message_with_inserts = re.sub(r'(?<!%)%([1-9]\d*)', replace_param, message)
                    # message_with_inserts = re.sub(r'(?<!%)%({.+?}|[1-9]\d*)', replace_param, message) # More complex regex
                    message_with_inserts = message_with_inserts.replace('%%', '%') # Handle escaped percent signs

                    if message_with_inserts != message:
                         self._print_debug(f"Fallback parameter insertion applied: {message_with_inserts[:100]}...")
                         message = message_with_inserts


                except Exception as insert_e:
                    self._print_debug(f"Error during fallback parameter insertion: {insert_e}")
                    # Keep the unformatted message if insertion fails

            # Assign the final message or a placeholder
            if message and message.strip():
                event_data["RenderedMessage"] = message.strip() # Strip whitespace
            else:
                # Try PowerShell fallback only if FormatMessage failed completely
                 self._print_debug(f"FormatMessage failed/returned empty for {cache_key}, trying PowerShell fallback.")
                 fallback_message = self._powershell_render_message_by_event_id(event_id)
                 if fallback_message:
                     event_data["RenderedMessage"] = fallback_message
                 else:
                     event_data["RenderedMessage"] = "[Message Rendering Failed]" # More specific than Unavailable

        except Exception as e:
            self._print_debug(f"Unhandled error in _add_rendered_message: {e}")
            if self.debug:
                import traceback
                traceback.print_exc(file=sys.stderr)
            event_data["RenderedMessage"] = "[Message Rendering Error]"


    def _parse_xml_to_dict(self, xml_str: str) -> Dict[str, Any]:
        """
        Parse XML string to dictionary using minidom. Handles potential errors.

        Args:
            xml_str: XML string from EVTX record

        Returns:
            Dictionary with extracted event data, or an error dict if parsing fails.
        """
        try:
            # Pre-process to remove default namespace which can simplify minidom access
            # Be careful as this might break XML with multiple namespaces.
            import re
            xml_str_no_ns = re.sub(' xmlns="[^"]+"', '', xml_str, count=1)

            dom = xml.dom.minidom.parseString(xml_str_no_ns) # Parse the cleaned XML
            event_elements = dom.getElementsByTagName("Event")
            if not event_elements:
                 raise ValueError("No <Event> tag found in XML record.")
            event = event_elements[0]

            system_elements = event.getElementsByTagName("System")
            if not system_elements:
                 raise ValueError("No <System> tag found within <Event>.")
            system = system_elements[0] # Assume one System element

            # --- Extract System Data ---
            provider_element = system.getElementsByTagName("Provider")
            provider_data = {
                "Name": provider_element[0].getAttribute("Name") if provider_element and provider_element[0].hasAttribute("Name") else None,
                "Guid": provider_element[0].getAttribute("Guid") if provider_element and provider_element[0].hasAttribute("Guid") else None,
                "EventSourceName": provider_element[0].getAttribute("EventSourceName") if provider_element and provider_element[0].hasAttribute("EventSourceName") else None
            }
            provider_data = {k: v for k, v in provider_data.items() if v is not None} # Clean None values

            event_id_element = system.getElementsByTagName("EventID")
            event_id_data = {
                "Value": self._get_element_text(system, "EventID"),
                "Qualifiers": event_id_element[0].getAttribute("Qualifiers") if event_id_element and event_id_element[0].hasAttribute("Qualifiers") else None
            }
            event_id_data = {k: v for k, v in event_id_data.items() if v is not None}

            time_created_element = system.getElementsByTagName("TimeCreated")
            time_created_data = {
                "SystemTime": time_created_element[0].getAttribute("SystemTime") if time_created_element and time_created_element[0].hasAttribute("SystemTime") else None
            }
            time_created_data = {k: v for k, v in time_created_data.items() if v is not None}


            correlation_element = system.getElementsByTagName("Correlation")
            correlation_data = {
                 "ActivityID": correlation_element[0].getAttribute("ActivityID") if correlation_element and correlation_element[0].hasAttribute("ActivityID") else None,
                 "RelatedActivityID": correlation_element[0].getAttribute("RelatedActivityID") if correlation_element and correlation_element[0].hasAttribute("RelatedActivityID") else None
             }
            correlation_data = {k: v for k, v in correlation_data.items() if v is not None} or None


            execution_element = system.getElementsByTagName("Execution")
            execution_data = {
                "ProcessID": execution_element[0].getAttribute("ProcessID") if execution_element and execution_element[0].hasAttribute("ProcessID") else None,
                "ThreadID": execution_element[0].getAttribute("ThreadID") if execution_element and execution_element[0].hasAttribute("ThreadID") else None
            }
            execution_data = {k: v for k, v in execution_data.items() if v is not None} or None


            security_element = system.getElementsByTagName("Security")
            security_data = {
                "UserID": security_element[0].getAttribute("UserID") if security_element and security_element[0].hasAttribute("UserID") else None
            }
            security_data = {k: v for k, v in security_data.items() if v is not None} or None

            system_data = {
                "Provider": provider_data or None, # Ensure None if empty dict
                "EventID": event_id_data or None,
                "Version": self._get_element_text(system, "Version"),
                "Level": self._get_element_text(system, "Level"),
                "Task": self._get_element_text(system, "Task"),
                "Opcode": self._get_element_text(system, "Opcode"),
                "Keywords": self._get_element_text(system, "Keywords"),
                "TimeCreated": time_created_data or None,
                "EventRecordID": self._get_element_text(system, "EventRecordID"),
                "Correlation": correlation_data, # Already handles None
                "Execution": execution_data, # Already handles None
                "Channel": self._get_element_text(system, "Channel"),
                "Computer": self._get_element_text(system, "Computer"),
                "Security": security_data # Already handles None
            }
             # Clean up top-level None values in System data
            system_data = {k: v for k, v in system_data.items() if v is not None}


            # --- Extract EventData ---
            event_data_nodes = event.getElementsByTagName("EventData")
            parsed_event_data = None
            if event_data_nodes:
                data_elements = event_data_nodes[0].getElementsByTagName("Data")
                if data_elements:
                    event_data_dict = {}
                    name_counts = {} # Handle duplicate names by converting to list
                    for idx, data in enumerate(data_elements):
                        name = data.getAttribute("Name")
                        # Safely get text content, handling potential None
                        value_node = data.firstChild
                        value = value_node.nodeValue.strip() if value_node and value_node.nodeType == value_node.TEXT_NODE else ""

                        if name:
                            if name in event_data_dict: # Duplicate name found
                                if name not in name_counts: # First duplicate
                                     # Convert original entry to list
                                     event_data_dict[name] = [event_data_dict[name]]
                                     name_counts[name] = 1 # Mark as list
                                # Append new value to list
                                event_data_dict[name].append(value)
                            else: # Unique name
                                event_data_dict[name] = value
                        else:
                            # Fallback to indexed key like Data_0, Data_1 if no name attribute
                            event_data_dict[f"Data_{idx}"] = value
                    parsed_event_data = event_data_dict
                else:
                    # Handle case where EventData has direct text content (less common)
                     text_content = "".join(t.nodeValue for t in event_data_nodes[0].childNodes if t.nodeType == t.TEXT_NODE).strip()
                     if text_content:
                        # Simple split by newline, could be more robust
                        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
                        parsed_event_data = {f"Data_{i}": value for i, value in enumerate(lines)}


            # --- Extract UserData ---
            # UserData often contains complex, schema-specific XML.
            # Parsing it generically can be tricky. A structured dict is attempted.
            user_data_nodes = event.getElementsByTagName("UserData")
            parsed_user_data = None
            if user_data_nodes:
                parsed_user_data = self._parse_complex_node(user_data_nodes[0])


            # --- Extract RenderingInfo (if available, often redundant if rendering separately) ---
            # This might exist if the event was logged with pre-rendered info
            rendering_info_nodes = event.getElementsByTagName("RenderingInfo")
            parsed_rendering_info = None
            if rendering_info_nodes:
                parsed_rendering_info = self._parse_rendering_info(rendering_info_nodes[0])


            # --- Extract Binary Data (usually Base64 encoded) ---
            binary_nodes = event.getElementsByTagName("Binary")
            binary_data = binary_nodes[0].firstChild.nodeValue if binary_nodes and binary_nodes[0].firstChild else None


            result = {
                "System": system_data,
                "EventData": parsed_event_data,
                "UserData": parsed_user_data,
                "RenderingInfo": parsed_rendering_info,
                "Binary": binary_data,
            }

            # Clean top-level None values for a cleaner structure
            result = {k: v for k, v in result.items() if v is not None}

            return result

        except Exception as e:
             # Log the error and the problematic XML for debugging
             self._print_debug(f"Failed to parse XML record: {e}\n--- XML Start ---\n{xml_str}\n--- XML End ---")
             if self.debug:
                 import traceback
                 traceback.print_exc(file=sys.stderr)
             # Return a structured error instead of raising, so processing can continue
             return {"error": "XML parsing failed", "details": str(e)}
             # Or optionally raise: raise ValueError(f"Failed to parse XML record: {e}") from e


    def _parse_complex_node(self, node) -> Any:
        """Generic recursive parser for potentially complex XML nodes like UserData."""
        # 1. Handle Text Nodes
        if node.nodeType == node.TEXT_NODE:
            return node.nodeValue.strip()
        # 2. Handle Element Nodes
        elif node.nodeType == node.ELEMENT_NODE:
            result: Dict[str, Any] = {}
            # Add attributes if any
            if node.attributes:
                 for attr_name in node.attributes.keys():
                     result[f"@{attr_name}"] = node.getAttribute(attr_name) # Prefix attributes

            # Process child nodes
            child_counts = {}
            text_content = ""
            has_element_children = False
            for child in node.childNodes:
                if child.nodeType == child.ELEMENT_NODE:
                    has_element_children = True
                    child_name = child.tagName # Use tagName for element nodes
                    child_data = self._parse_complex_node(child)

                    # Handle repeated elements by creating lists
                    if child_name in result:
                        if child_name not in child_counts: # First repeat
                            result[child_name] = [result[child_name]] # Convert existing to list
                            child_counts[child_name] = 2
                        else:
                            result[child_name].append(child_data)
                            child_counts[child_name] += 1
                    else:
                        result[child_name] = child_data
                elif child.nodeType == child.TEXT_NODE:
                     cleaned_text = child.nodeValue.strip()
                     if cleaned_text:
                          text_content += cleaned_text


            # Decide what to return
            if not has_element_children and not result and text_content:
                 return text_content # Element with only text content
            elif not has_element_children and result and not text_content:
                 return result # Element with only attributes
            else:
                 # Element with children/attributes and potentially text
                 if text_content:
                      result['#text'] = text_content # Add mixed content text under #text key
                 return result or None # Return None if result is empty


        # 3. Handle other node types (like comments, etc.) - ignore for now
        return None


    def _parse_rendering_info(self, rendering_info_node) -> Dict[str, Any]:
        """Parse the standard RenderingInfo node structure."""
        result = {
            "Message": self._get_element_text(rendering_info_node, "Message"),
            "Level": self._get_element_text(rendering_info_node, "Level"),
            "Task": self._get_element_text(rendering_info_node, "Task"),
            "Opcode": self._get_element_text(rendering_info_node, "Opcode"),
            "Channel": self._get_element_text(rendering_info_node, "Channel"),
            "Provider": self._get_element_text(rendering_info_node, "Provider"),
            "Keywords": None # Initialize keywords
        }

        keywords_nodes = rendering_info_node.getElementsByTagName("Keywords")
        if keywords_nodes:
            keywords_list = []
            for kw_node in keywords_nodes[0].getElementsByTagName("Keyword"):
                 kw_text = self._get_element_text(kw_node, "Keyword") # Pass node itself
                 if kw_text:
                     keywords_list.append(kw_text)
            if keywords_list:
                result["Keywords"] = keywords_list

        # Clean None values before returning
        return {k: v for k, v in result.items() if v}


    def _get_element_text(self, parent, tag_name: str) -> Optional[str]:
        """Safely extract text from the first matching child XML element."""
        try:
            elements = parent.getElementsByTagName(tag_name)
            if elements and elements[0].firstChild and elements[0].firstChild.nodeType == elements[0].TEXT_NODE:
                 # Collapse potentially multiple whitespace characters into single spaces
                 return ' '.join(elements[0].firstChild.nodeValue.split())
        except IndexError:
             self._print_debug(f"Tag '{tag_name}' not found under parent.")
             pass # Element not found
        except Exception as e:
            self._print_debug(f"Error getting text for tag '{tag_name}': {e}")
        return None # Return None if not found or error


    def _get_element_attribute(self, parent, tag_name: str, attr_name: str) -> Optional[str]:
        """Safely extract attribute value from the first matching child XML element."""
        try:
            elements = parent.getElementsByTagName(tag_name)
            if elements and elements[0].hasAttribute(attr_name):
                return elements[0].getAttribute(attr_name)
        except IndexError:
             self._print_debug(f"Tag '{tag_name}' not found for attribute '{attr_name}'.")
             pass # Element not found
        except Exception as e:
            self._print_debug(f"Error getting attribute '{attr_name}' for tag '{tag_name}': {e}")
        return None # Return None if not found or error


# --- Command Line Interface (for testing the script directly) ---
def main():
    parser = argparse.ArgumentParser(description="Parse EVTX files and output as formatted text.")
    parser.add_argument("input_file", help="Path to the input EVTX file.")
    # parser.add_argument("-o", "--output", required=True, help="Path to the output file (JSON format).") # Output is now stdout
    parser.add_argument("-l", "--limit", type=int, default=None, help="Maximum number of records to process.")
    parser.add_argument("--no-render", action="store_true", help="Disable rendering of event messages (faster, less info).")
    parser.add_argument("--debug", action="store_true", help="Enable debug printing to stderr.")

    args = parser.parse_args()

    try:
        evtx_parser = EvtxParser(args.input_file, debug=args.debug)
        # Call the primary text extraction method
        text_output = evtx_parser.get_records_as_text(
            limit=args.limit,
            render_messages=not args.no_render
        )
        # Print the final text block to stdout
        print(text_output)
        # Indicate success via exit code
        sys.exit(0)

    except FileNotFoundError as e:
        # Output errors as JSON to stderr for the calling process
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        error_msg = f"An unexpected error occurred: {e}"
        print(json.dumps({"error": error_msg}), file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc(file=sys.stderr) # Print traceback to stderr if debug
        sys.exit(1)

if __name__ == "__main__":
    main()
