import os
import faiss
import numpy as np
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import FAISS
from langchain.docstore.document import Document
from typing import List, Optional, Dict, Any

# --- Constants ---
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
FAISS_INDEX_PATH = "./faiss_index" # Directory to save the index

class VectorStoreManager:
    """Manages the FAISS vector store and embeddings."""

    def __init__(self, index_path: str = FAISS_INDEX_PATH):
        """
        Initializes the VectorStoreManager.

        Args:
            index_path: The path to save/load the FAISS index.
        """
        self.index_path = index_path
        self.embeddings_model = self._get_embeddings_model()
        self.vector_store = self._load_vector_store()
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
        )
        print(f"VectorStoreManager initialized. Index path: {self.index_path}")
        print(f"Current vector store size (documents): {self.vector_store.index.ntotal if self.vector_store and self.vector_store.index else 0}")


    def _get_embeddings_model(self) -> GoogleGenerativeAIEmbeddings:
        """Initializes and returns the Google Generative AI Embeddings model."""
        api_key = os.getenv("GOOGLE_GENAI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_GENAI_API_KEY not found in environment variables.")
        # Use the recommended text-embedding-004 model
        return GoogleGenerativeAIEmbeddings(model="models/text-embedding-004", google_api_key=api_key)

    def _load_vector_store(self) -> Optional[FAISS]:
        """Loads an existing FAISS index from disk if available."""
        if os.path.exists(f"{self.index_path}.faiss") and os.path.exists(f"{self.index_path}.pkl"):
            try:
                print(f"Loading existing FAISS index from {self.index_path}")
                # Allow dangerous deserialization, as we trust the source (our own saved index)
                # In a production scenario with untrusted sources, this needs careful consideration.
                return FAISS.load_local(
                    self.index_path,
                    self.embeddings_model,
                    allow_dangerous_deserialization=True # Required for FAISS pickle loading
                 )
            except Exception as e:
                print(f"Error loading FAISS index: {e}. Creating a new one.")
                # If loading fails (e.g., corrupted file, version mismatch), delete old files
                if os.path.exists(f"{self.index_path}.faiss"): os.remove(f"{self.index_path}.faiss")
                if os.path.exists(f"{self.index_path}.pkl"): os.remove(f"{self.index_path}.pkl")
                return None
        else:
            print("No existing FAISS index found. A new one will be created upon adding documents.")
            return None

    def _save_vector_store(self):
        """Saves the current FAISS index to disk."""
        if self.vector_store:
            try:
                print(f"Saving FAISS index to {self.index_path}")
                self.vector_store.save_local(self.index_path)
            except Exception as e:
                print(f"Error saving FAISS index: {e}")
        else:
            print("No vector store to save.")

    def add_document(self, doc_id: str, content: str, metadata: Dict[str, Any]):
        """
        Splits document content, creates embeddings, and adds them to the vector store.

        Args:
            doc_id: A unique identifier for the document.
            content: The text content of the document.
            metadata: Metadata associated with the document (e.g., source filename).
        """
        if not content:
            print(f"Warning: Content for document {metadata.get('source', doc_id)} is empty. Skipping.")
            return

        print(f"Splitting document: {metadata.get('source', doc_id)}")
        # Add the doc_id to the metadata of each chunk for later removal
        chunk_metadata = {**metadata, "doc_id": doc_id}
        chunks = self.text_splitter.create_documents([content], metadatas=[chunk_metadata]) # Pass metadata here

        if not chunks:
            print(f"Warning: No chunks created for document {metadata.get('source', doc_id)}. Content might be too short or only whitespace.")
            return

        print(f"Created {len(chunks)} chunks. Adding to vector store...")

        # Add chunks to the store
        try:
            if self.vector_store is None:
                 # Create index from the first set of documents
                 print("Creating new FAISS index.")
                 self.vector_store = FAISS.from_documents(chunks, self.embeddings_model)
            else:
                 # Add to existing index
                 self.vector_store.add_documents(chunks)

            self._save_vector_store() # Save after adding documents
            print(f"Successfully added chunks for {metadata.get('source', doc_id)} to vector store.")
            print(f"New vector store size (documents/chunks): {self.vector_store.index.ntotal}")

        except Exception as e:
             print(f"Error adding document chunks to FAISS for {metadata.get('source', doc_id)}: {e}")
             # Consider whether to rollback or leave potentially partial additions

    def remove_document(self, doc_id: str):
        """
        Removes all chunks associated with a specific doc_id from the vector store.

        Args:
            doc_id: The unique identifier of the document to remove.
        """
        if not self.vector_store or not hasattr(self.vector_store, 'docstore'):
            print("Vector store is not initialized or doesn't have a docstore. Cannot remove document.")
            return

        # Find the FAISS index IDs associated with the doc_id
        ids_to_remove = [
            index_id for index_id, doc in self.vector_store.docstore._dict.items()
            if doc.metadata.get("doc_id") == doc_id
        ]

        if not ids_to_remove:
            print(f"No chunks found in vector store for doc_id: {doc_id}")
            return

        print(f"Removing {len(ids_to_remove)} chunks for doc_id: {doc_id}")

        try:
            # Remove the vectors from the FAISS index and the docstore
            removed_count = self.vector_store.delete(ids_to_remove)

            if removed_count:
                print(f"Successfully removed {len(ids_to_remove)} chunks for doc_id: {doc_id}.")
                self._save_vector_store() # Save the index after removal
                print(f"New vector store size (documents/chunks): {self.vector_store.index.ntotal}")
            else:
                 print(f"Warning: Attempted to remove chunks for doc_id {doc_id}, but delete operation returned False.")

        except Exception as e:
            print(f"Error removing document chunks for doc_id {doc_id}: {e}")


    def get_retriever(self, search_kwargs={"k": 5}):
        """
        Returns a retriever instance for the vector store.

        Args:
            search_kwargs: Arguments to pass to the retriever's search function (e.g., k for number of results).

        Returns:
            A Langchain retriever instance.
        """
        if self.vector_store:
            return self.vector_store.as_retriever(search_kwargs=search_kwargs)
        else:
            # Return a dummy retriever or raise an error if no store exists
            print("Warning: Vector store not initialized. Returning None for retriever.")
            # raise ValueError("Vector store not initialized. Cannot create retriever.")
            return None

    def get_store_size(self) -> int:
        """Returns the total number of vectors/chunks in the store."""
        if self.vector_store and hasattr(self.vector_store, 'index'):
            return self.vector_store.index.ntotal
        return 0
