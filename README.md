# Document Insights (Python/Streamlit Version)

This is a Python application built with Streamlit that allows you to upload documents (PDF, TXT, EVTX), index them, and ask questions about their content using a Retrieval-Augmented Generation (RAG) approach with Google's Gemini models.

## Setup

1.  **Create a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *   **Note for EVTX parsing:** The application uses `python-evtx`. On Windows, it can optionally use `pywin32` for better event message rendering. If you need this and are on Windows, install it separately: `pip install pywin32`.
    *   **Note for FAISS:** The `requirements.txt` includes `faiss-cpu`. If you have a compatible GPU and CUDA installed, you can install `faiss-gpu` instead for potentially faster vector search.

3.  **Set up API Key:**
    *   Create a `.env` file in the project root.
    *   Add your Google AI API key to the `.env` file:
        ```
        GOOGLE_GENAI_API_KEY="YOUR_API_KEY_HERE"
        ```
        *(Get your key from Google AI Studio: https://aistudio.google.com/app/apikey)*

4.  **Prepare Documents (Optional):**
    *   Create a directory named `docs` in the project root.
    *   Place any PDF, TXT, or EVTX files you want to be indexed automatically on startup into this `docs` directory.

## Running the Application

```bash
streamlit run main.py
```

The application will open in your web browser.

## Features

*   **Document Upload:** Upload PDF, TXT, and EVTX files via the sidebar.
*   **Automatic Indexing:** Documents in the `./docs` directory are automatically indexed when the app starts.
*   **RAG Chat:** Ask questions about the content of all indexed documents. The AI will retrieve relevant snippets and use them to generate an answer.
*   **Source Display:** View the specific document chunks used by the AI to generate its response.
*   **Progress Tracking:** See the status of document processing and indexing in the sidebar.
*   **EVTX Parsing:** EVTX files are parsed into a textual representation using a dedicated Python script before indexing.
