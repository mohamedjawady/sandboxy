import streamlit as st
import os
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Tuple, Dict, Any, Optional

from document_processor import process_document, get_doc_type
from vector_store_manager import VectorStoreManager
from rag_chain import setup_rag_chain, ask_question

# --- Constants ---
DOCS_DIRECTORY = Path("./docs")
ALLOWED_EXTENSIONS = ["pdf", "txt", "evtx"]
DOC_TYPE_ICONS = {
    "pdf": "📄",
    "txt": "📝",
    "evtx": "🔍",
    "error": "⚠️",
    "processing": "⏳",
    "indexed": "✅",
    "queued": "📨",
    "initial": "💾" # Icon for initially loaded docs
}

# --- Load Environment Variables ---
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_GENAI_API_KEY")

if not GOOGLE_API_KEY:
    st.error("🔴 Error: GOOGLE_GENAI_API_KEY environment variable not set.")
    st.info("Please create a `.env` file in the root directory and add your key:")
    st.code("GOOGLE_GENAI_API_KEY=\"YOUR_API_KEY_HERE\"")
    st.stop()


# --- Streamlit Page Configuration ---
st.set_page_config(
    page_title="Document Insights",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Initialize Session State ---
if "vector_store_manager" not in st.session_state:
    st.session_state.vector_store_manager = VectorStoreManager()
    print("Initialized VectorStoreManager.")

if "rag_chain" not in st.session_state:
    st.session_state.rag_chain = setup_rag_chain(st.session_state.vector_store_manager.get_retriever())
    print("Initialized RAG Chain.")

if "documents" not in st.session_state:
    # Store document metadata: {id: {"name": str, "size": int, "type": str, "status": str, "error": str|None, "is_initial": bool}}
    st.session_state.documents = {}
    print("Initialized documents state.")

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Ask me anything about the indexed documents!", "sources": None}]
    print("Initialized chat messages.")

if "processing_queue" not in st.session_state:
    st.session_state.processing_queue = [] # List of document IDs to process
    print("Initialized processing queue.")

if "is_processing" not in st.session_state:
    st.session_state.is_processing = False
    print("Initialized processing flag.")

if "initial_load_done" not in st.session_state:
    st.session_state.initial_load_done = False
    print("Initialized initial load flag.")


# --- Helper Functions ---
def update_doc_status(doc_id: str, status: str, error: Optional[str] = None):
    """Updates the status and optionally error message of a document in session state."""
    if doc_id in st.session_state.documents:
        st.session_state.documents[doc_id]["status"] = status
        st.session_state.documents[doc_id]["error"] = error
        # Use st.rerun() carefully, might cause loops if not managed well
        # Consider using st.experimental_rerun() in newer Streamlit versions if needed
        # For now, rely on Streamlit's natural rerun cycle triggered by state changes
    else:
        print(f"Warning: Tried to update status for non-existent doc_id: {doc_id}")


def add_document_to_state(doc_id: str, name: str, size: int, type: str, status: str, is_initial: bool = False):
    """Adds a new document's metadata to the session state."""
    if doc_id not in st.session_state.documents:
        st.session_state.documents[doc_id] = {
            "name": name,
            "size": size,
            "type": type,
            "status": status,
            "error": None,
            "is_initial": is_initial
        }


def format_bytes(size_bytes: int) -> str:
    """Formats file size in bytes to a human-readable string."""
    if size_bytes == 0:
       return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def display_progress(container):
    """Displays overall processing progress in the sidebar."""
    processing_docs = [doc for doc in st.session_state.documents.values() if doc["status"] in ["processing", "queued"]]
    error_docs = [doc for doc in st.session_state.documents.values() if doc["status"] == "error"]
    total_docs = len(st.session_state.documents)
    indexed_docs = total_docs - len(processing_docs) - len(error_docs)

    with container:
        if not st.session_state.initial_load_done and not total_docs:
             st.info("Loading initial documents...")
             st.progress(0)
        elif st.session_state.is_processing or len(processing_docs) > 0:
            status_text = f"Processing {len(processing_docs)} document(s)..."
            if len(error_docs) > 0:
                status_text += f" ({len(error_docs)} error(s))"
            st.info(status_text)
            # Simple progress (doesn't reflect actual chunk progress)
            progress_value = int((indexed_docs / total_docs) * 100) if total_docs > 0 else 0
            st.progress(progress_value)
        elif len(error_docs) > 0:
            st.error(f"{len(error_docs)} document(s) failed to process.")
        elif total_docs > 0:
            st.success(f"✅ All {indexed_docs} documents indexed.")
        else:
            st.write("Upload documents to start.")

def process_queue():
    """Processes documents added to the queue one by one."""
    if st.session_state.processing_queue:
        st.session_state.is_processing = True
        doc_id_to_process = st.session_state.processing_queue.pop(0) # Get the next doc ID

        doc_info = st.session_state.documents.get(doc_id_to_process)
        if not doc_info:
             print(f"Error: Document ID {doc_id_to_process} not found in state during queue processing.")
             # If queue is now empty, reset processing flag
             if not st.session_state.processing_queue:
                 st.session_state.is_processing = False
             return # Skip this item

        uploaded_file_obj = doc_info.get("file_obj") # Assumes file object is stored temporarily
        file_path_obj = doc_info.get("path")

        if not uploaded_file_obj and not file_path_obj:
            print(f"Error: No file content source for Document ID {doc_id_to_process}.")
            update_doc_status(doc_id_to_process, "error", "Missing file content source.")
            if not st.session_state.processing_queue:
                st.session_state.is_processing = False
            return # Skip

        print(f"Processing queued document: {doc_info['name']} (ID: {doc_id_to_process})")
        update_doc_status(doc_id_to_process, "processing")

        try:
            # Process the document (text extraction)
            if uploaded_file_obj:
                content, error = process_document(doc_info["name"], file_content=uploaded_file_obj.getvalue())
            elif file_path_obj:
                 content, error = process_document(doc_info["name"], file_path=file_path_obj)
            else: # Should be caught above, but as a safeguard
                 raise ValueError("No file content source available")

            if error or not content:
                raise Exception(error or "Text extraction failed or produced no content.")

            # Index the extracted content
            print(f"Indexing document: {doc_info['name']} (ID: {doc_id_to_process})")
            st.session_state.vector_store_manager.add_document(
                doc_id=doc_id_to_process,
                content=content,
                metadata={"source": doc_info["name"], "type": doc_info["type"]}
            )
            update_doc_status(doc_id_to_process, "indexed")
            print(f"Successfully indexed: {doc_info['name']}")
            st.toast(f"✅ Indexed: {doc_info['name']}", icon="📄")

        except Exception as e:
            print(f"Error processing document {doc_info['name']} (ID: {doc_id_to_process}): {e}")
            update_doc_status(doc_id_to_process, "error", str(e))
            st.toast(f"⚠️ Error processing {doc_info['name']}: {e}", icon="🔥")
        finally:
             # Clean up temporary file object if it exists
            if "file_obj" in st.session_state.documents[doc_id_to_process]:
                 del st.session_state.documents[doc_id_to_process]["file_obj"]
            # If queue is now empty, reset processing flag
            if not st.session_state.processing_queue:
                st.session_state.is_processing = False
                print("Processing queue finished.")
                # Trigger one final rerun to update UI elements blocked by is_processing
                st.rerun()

# --- Initial Document Loading ---
def load_initial_documents():
    """Loads and queues documents from the DOCS_DIRECTORY."""
    print(f"Checking for initial documents in: {DOCS_DIRECTORY}")
    if DOCS_DIRECTORY.exists() and DOCS_DIRECTORY.is_dir():
        queued_count = 0
        for filepath in DOCS_DIRECTORY.iterdir():
            if filepath.is_file():
                file_ext = filepath.suffix.lower().strip('.')
                if file_ext in ALLOWED_EXTENSIONS:
                    doc_id = str(filepath.resolve()) # Use resolved path as unique ID
                    if doc_id not in st.session_state.documents:
                        try:
                            file_size = filepath.stat().st_size
                            add_document_to_state(
                                doc_id=doc_id,
                                name=filepath.name,
                                size=file_size,
                                type=file_ext,
                                status="queued",
                                is_initial=True
                            )
                            # Store path for processing
                            st.session_state.documents[doc_id]["path"] = filepath
                            st.session_state.processing_queue.append(doc_id)
                            queued_count += 1
                            print(f"Queued initial document: {filepath.name}")
                        except Exception as e:
                             print(f"Error adding initial document {filepath.name} to state: {e}")
                    else:
                         print(f"Skipping already known initial document: {filepath.name}")

        if queued_count > 0:
             print(f"Added {queued_count} initial documents to the processing queue.")
        else:
            print("No new initial documents found to queue.")
    else:
        print(f"Docs directory '{DOCS_DIRECTORY}' not found. Skipping initial load.")
    st.session_state.initial_load_done = True


# --- Streamlit UI ---

# --- Sidebar ---
with st.sidebar:
    st.title("🧠 RAG Insights")
    st.caption("Upload, manage, and query documents.")

    uploaded_files = st.file_uploader(
        "Upload Documents",
        type=ALLOWED_EXTENSIONS,
        accept_multiple_files=True,
        help="Supports PDF, TXT, and EVTX files.",
        disabled=st.session_state.is_processing,
        label_visibility="collapsed" # Keep label for accessibility but hide visually
    )

    if uploaded_files:
        new_files_queued = 0
        for uploaded_file in uploaded_files:
            doc_id = f"upload-{uuid.uuid4()}-{uploaded_file.name}"
            file_type = get_doc_type(uploaded_file.name)
            if doc_id not in st.session_state.documents and file_type in ALLOWED_EXTENSIONS:
                 add_document_to_state(
                     doc_id=doc_id,
                     name=uploaded_file.name,
                     size=uploaded_file.size,
                     type=file_type,
                     status="queued",
                     is_initial=False
                 )
                 # Store file object temporarily for processing
                 st.session_state.documents[doc_id]["file_obj"] = uploaded_file
                 st.session_state.processing_queue.append(doc_id)
                 new_files_queued += 1
            else:
                print(f"Skipping already uploaded or invalid file: {uploaded_file.name}")

        if new_files_queued > 0:
            st.toast(f"Queued {new_files_queued} new document(s) for processing.", icon="📨")
            # Clear the uploader state after processing the files
            # This might require a rerun or specific handling in Streamlit if not clearing automatically
        # No else needed, handled by the loop condition

    st.divider()

    # Document List Area
    st.subheader("Indexed Documents")
    doc_list_container = st.container(height=300) # Make list scrollable

    with doc_list_container:
        if not st.session_state.documents:
            st.caption("No documents loaded yet.")
        else:
            # Sort documents: initial first, then by name
            sorted_doc_ids = sorted(
                st.session_state.documents.keys(),
                key=lambda id: (not st.session_state.documents[id]['is_initial'], st.session_state.documents[id]['name'])
            )
            for doc_id in sorted_doc_ids:
                doc = st.session_state.documents[doc_id]
                icon = DOC_TYPE_ICONS.get(doc["status"], "❓")
                if doc["status"] != "error" and doc["status"] != "processing" and doc["status"] != "queued":
                     icon = DOC_TYPE_ICONS.get(doc["type"], "❓") # Use file type icon if indexed

                # Tooltip content
                tooltip_text = f"Status: {doc['status'].capitalize()}"
                if doc['error']:
                     tooltip_text += f"\nError: {doc['error']}"
                if doc['is_initial']:
                     tooltip_text += "\n(Loaded from /docs)"

                col1, col2 = st.columns([0.8, 0.2])
                with col1:
                     # Add initial icon if applicable
                     initial_icon = DOC_TYPE_ICONS.get("initial", "") + " " if doc['is_initial'] else ""
                     st.markdown(f"<span title='{tooltip_text}'>{icon} {initial_icon}{doc['name']}</span>", unsafe_allow_html=True)
                     # st.caption(f"{format_bytes(doc['size'])}") # Optional: Show size

                with col2:
                    if not doc["is_initial"] and doc["status"] != "processing": # Allow deleting non-initial, non-processing docs
                        delete_key = f"delete-{doc_id}"
                        if st.button("🗑️", key=delete_key, help=f"Delete {doc['name']} from index"):
                            try:
                                st.session_state.vector_store_manager.remove_document(doc_id)
                                del st.session_state.documents[doc_id]
                                st.toast(f"Deleted {doc['name']}", icon="🗑️")
                                st.rerun() # Rerun to update the list immediately
                            except Exception as e:
                                st.error(f"Failed to delete {doc['name']}: {e}")

    st.divider()

    # Progress Tracking Area
    progress_container = st.container()
    display_progress(progress_container)


# --- Main Chat Area ---
st.header("Chat with your Documents")
st.caption("Ask questions based on the content of all indexed documents.")

# Display chat messages
for message in st.session_state.messages:
    avatar = "🧠" if message["role"] == "assistant" else "👤"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        # Display sources if available
        if message.get("sources"):
            with st.expander("View Sources"):
                for i, source_doc in enumerate(message["sources"]):
                    try:
                        # Safely access metadata and content
                        metadata = source_doc.metadata
                        page_content = source_doc.page_content
                        source_name = metadata.get('source', 'Unknown Source')
                        source_type = metadata.get('type', 'N/A').upper()

                        st.markdown(f"**Source {i+1}: {source_name} ({source_type})**")
                        # Display snippet of the content
                        st.text_area(
                            f"Content Snippet {i+1}",
                            page_content,
                            height=100,
                            key=f"source_{message['id']}_{i}", # Unique key for text area
                            disabled=True # Make it read-only
                        )
                        st.divider()
                    except Exception as e:
                        st.warning(f"Could not display source {i+1}: {e}")


# Chat input
prompt = st.chat_input(
    "Ask a question...",
    disabled=(st.session_state.is_processing or not st.session_state.documents or not any(d['status'] == 'indexed' for d in st.session_state.documents.values()))
)

if prompt:
    if not st.session_state.documents or not any(d['status'] == 'indexed' for d in st.session_state.documents.values()):
         st.warning("Please wait for documents to be indexed before asking questions.")
    elif st.session_state.is_processing:
         st.warning("Please wait for document processing to complete.")
    else:
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt, "sources": None})
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt)

        # Get assistant response
        with st.chat_message("assistant", avatar="🧠"):
            message_placeholder = st.empty()
            message_placeholder.markdown("Thinking...⏳")
            try:
                # Ensure RAG chain uses the latest retriever state
                st.session_state.rag_chain = setup_rag_chain(st.session_state.vector_store_manager.get_retriever())

                full_response, sources = ask_question(st.session_state.rag_chain, prompt)
                message_placeholder.markdown(full_response)
                # Add assistant response with sources
                st.session_state.messages.append({"role": "assistant", "content": full_response, "sources": sources})

            except Exception as e:
                error_message = f"Sorry, an error occurred: {e}"
                message_placeholder.error(error_message)
                st.session_state.messages.append({"role": "assistant", "content": error_message, "sources": None})


# --- Background Processing Trigger ---
# Load initial docs only once after the app starts and state is ready
if not st.session_state.initial_load_done:
    load_initial_documents()

# Process the queue if not empty and not already processing something else
# This will run on script reruns if the queue has items
if st.session_state.processing_queue and not st.session_state.is_processing:
     # Use a small delay before starting queue processing to allow UI updates
     # time.sleep(0.1) # Removed as direct call is better
     process_queue() # Process one item from the queue

# Final check to ensure is_processing is false if the queue becomes empty
if not st.session_state.processing_queue and st.session_state.is_processing:
     # This might happen if processing finished but the flag wasn't reset
     # Or if the last item was removed manually somehow
     st.session_state.is_processing = False
     print("Resetting is_processing flag as queue is empty.")
     # Optionally rerun if needed
     # st.rerun()
