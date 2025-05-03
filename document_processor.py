import os
import subprocess
import tempfile
from pathlib import Path
import uuid
from typing import Optional, Tuple, Union, IO

# Attempt to import PyPDF
try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False
    print("Warning: pypdf not found. PDF processing will be basic text extraction.")
    print("Install using: pip install pypdf")


def get_doc_type(file_name: str) -> Optional[str]:
    """Determines the document type from the file extension."""
    ext = Path(file_name).suffix.lower().strip('.')
    if ext in ["pdf", "txt", "evtx"]:
        return ext
    return None

def parse_evtx_file_content(file_content: bytes, source_name_for_log: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses EVTX file content using the evtx_parser.py script.

    Args:
        file_content: The byte content of the EVTX file.
        source_name_for_log: The original name of the file for logging.

    Returns:
        A tuple containing (parsed_text, error_message).
        parsed_text is None if parsing fails.
        error_message contains the error details if parsing fails.
    """
    python_executable = os.environ.get('PYTHON_EXECUTABLE', 'python3') # Or 'python'
    script_path = Path(__file__).parent.parent / "scripts" / "evtx_parser.py" # Path relative to this file's parent

    if not script_path.exists():
        error_msg = f"EVTX parser script not found at expected location: {script_path}"
        print(f"ERROR: {error_msg}")
        return None, error_msg

    # Create a temporary file to hold the EVTX content
    temp_file_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".evtx") as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        print(f"EVTX content for '{source_name_for_log}' written to temp file: {temp_file_path}")

        # Execute the script
        args = [python_executable, str(script_path), temp_file_path, "--debug"] # Add --debug for more output
        print(f"Running command: {' '.join(args)}")
        process = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding='utf-8', # Explicitly set encoding
            timeout=120 # Add a generous timeout (e.g., 2 minutes)
        )

        print(f"EVTX parser stdout for '{source_name_for_log}':\n{process.stdout[:500]}...") # Log beginning of stdout
        if process.stderr:
            print(f"EVTX parser stderr for '{source_name_for_log}':\n{process.stderr}") # Log all stderr

        if process.returncode == 0:
            print(f"Successfully parsed EVTX: {source_name_for_log}")
            return process.stdout.strip(), None
        else:
            error_msg = f"EVTX parsing script failed for '{source_name_for_log}' (code {process.returncode}). Stderr: {process.stderr.strip()}"
            print(f"ERROR: {error_msg}")
            return None, error_msg

    except subprocess.TimeoutExpired:
         error_msg = f"EVTX parsing timed out for '{source_name_for_log}' after 120 seconds."
         print(f"ERROR: {error_msg}")
         return None, error_msg
    except FileNotFoundError:
         error_msg = f"Python executable '{python_executable}' not found. Ensure Python is installed and in PATH."
         print(f"ERROR: {error_msg}")
         return None, error_msg
    except Exception as e:
        error_msg = f"An unexpected error occurred during EVTX parsing for '{source_name_for_log}': {e}"
        print(f"ERROR: {error_msg}")
        return None, error_msg
    finally:
        # Clean up the temporary file
        if temp_file_path and Path(temp_file_path).exists():
            try:
                os.unlink(temp_file_path)
                print(f"Deleted temp file: {temp_file_path}")
            except OSError as e:
                print(f"Warning: Failed to delete temporary file {temp_file_path}: {e}")


def process_document(
    file_name: str,
    file_content: Optional[bytes] = None,
    file_path: Optional[Union[str, Path]] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Processes a document file (PDF, TXT, EVTX) to extract text content.

    Args:
        file_name: The original name of the file.
        file_content: The byte content of the file (for uploads).
        file_path: The path to the file (for initial loading).

    Returns:
        A tuple containing (extracted_text, error_message).
        extracted_text is None if extraction fails.
        error_message contains the error details if extraction fails.
    """
    doc_type = get_doc_type(file_name)
    if not doc_type:
        return None, f"Unsupported file type for file: {file_name}"

    if file_content is None and file_path:
        try:
            print(f"Reading content from path: {file_path}")
            with open(file_path, "rb") as f:
                file_content = f.read()
        except Exception as e:
            return None, f"Error reading file from path {file_path}: {e}"
    elif file_content is None:
         return None, "No file content or file path provided."

    print(f"Processing document: {file_name} (Type: {doc_type})")

    try:
        if doc_type == "txt":
            print("Extracting text from TXT...")
            # Attempt common encodings
            for encoding in ['utf-8', 'latin-1', 'windows-1252']:
                try:
                    text = file_content.decode(encoding)
                    print(f"Successfully decoded TXT with {encoding}")
                    return text, None
                except UnicodeDecodeError:
                    continue
            return None, "Could not decode TXT file with common encodings."

        elif doc_type == "pdf":
             if HAS_PYPDF:
                 print("Extracting text from PDF using pypdf...")
                 try:
                     reader = pypdf.PdfReader(io.BytesIO(file_content))
                     text = ""
                     for page in reader.pages:
                         text += page.extract_text() or "" # Add null check
                     if not text.strip():
                          print("Warning: pypdf extracted no text from PDF. It might be image-based or have encoding issues.")
                          # Fallback to basic bytes decoding as a last resort
                          return file_content.decode('latin-1', errors='ignore'), "pypdf extracted no text, basic extraction attempted."
                     print(f"Successfully extracted {len(text)} characters from PDF.")
                     return text, None
                 except Exception as e:
                      print(f"pypdf extraction failed: {e}. Falling back to basic extraction.")
                      # Fallback if pypdf fails
                      return file_content.decode('latin-1', errors='ignore'), f"pypdf failed: {e}, basic extraction attempted."
             else:
                 print("Extracting text from PDF (basic byte decoding)...")
                 # Basic extraction if pypdf is not available
                 return file_content.decode('latin-1', errors='ignore'), "Used basic text extraction (pypdf not installed)."

        elif doc_type == "evtx":
            print("Parsing EVTX file...")
            return parse_evtx_file_content(file_content, file_name)

        else:
            # Should not happen due to initial check, but good practice
            return None, f"Internal error: Reached unexpected document type {doc_type}"

    except Exception as e:
        error_msg = f"Error processing {file_name}: {e}"
        print(f"ERROR: {error_msg}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging
        return None, error_msg
    return None, f"Unknown error processing {file_name}" # Fallback error
