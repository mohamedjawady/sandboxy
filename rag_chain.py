from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain.schema import Document
from typing import List, Tuple, Any, Dict

def format_docs(docs: List[Document]) -> str:
    """Formats retrieved documents into a single string for the prompt."""
    formatted_docs = []
    for i, doc in enumerate(docs):
        metadata_str = f"Source: {doc.metadata.get('source', 'Unknown')}, Type: {doc.metadata.get('type', 'N/A').upper()}"
        formatted_docs.append(f"--- Snippet from Document {i+1} ({metadata_str}) ---\n{doc.page_content}\n---")
    return "\n".join(formatted_docs)

def setup_rag_chain(retriever):
    """
    Sets up the RAG chain using Langchain.

    Args:
        retriever: The vector store retriever instance.

    Returns:
        A Langchain Runnable sequence representing the RAG chain.
    """
    # Define the LLM model
    # model = ChatGoogleGenerativeAI(model="gemini-pro", temperature=0.7) # Older model
    model = ChatGoogleGenerativeAI(model="gemini-1.5-flash-latest", temperature=0.7) # Use flash model

    # Define the prompt template
    template = """You are an AI assistant designed to answer questions based *only* on the provided context snippets from various documents.

Context Snippets:
{context}

User Question: {question}

Instructions:
1. Carefully read the provided context snippets.
2. Formulate a concise and accurate answer to the user's question using *only* the information found in the context snippets.
3. If the context snippets do not contain enough information to answer the question, state that clearly. Do not make assumptions or use external knowledge.
4. Respond *only* with the answer. Do not include introductions like "Based on the context..." unless it's part of the answer itself.

Example (if info found):
The primary processing step involves text extraction using specific parsers.

Example (if info NOT found):
The provided context does not contain information about the project's deadline.

Answer:"""

    prompt = ChatPromptTemplate.from_template(template)

    # Define the RAG chain
    rag_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | model
        | StrOutputParser()
    )

    # Define a parallel chain to retrieve sources along with the answer
    rag_chain_with_source = RunnablePassthrough.assign(
        answer=rag_chain,
        sources=RunnableLambda(lambda x: retriever.get_relevant_documents(x['question'])) # Get sources based on original question
    )


    # Modify the chain to handle the input dictionary correctly for source retrieval
    def prepare_input(input_dict: Dict[str, Any]) -> str:
         # Extract the actual question string to pass to the retriever and RAG chain
        return input_dict['question']

    final_chain = RunnablePassthrough.assign(
         question=RunnableLambda(lambda x: x['question']) # Pass the original question through
     ) | {
         "answer": (RunnableLambda(prepare_input) | rag_chain), # rag_chain needs only the string query
         "sources": (RunnableLambda(prepare_input) | retriever) # retriever also needs only the string query
     }


    print("RAG Chain setup complete.")
    return final_chain


def ask_question(rag_chain, question: str) -> Tuple[str, List[Document]]:
    """
    Invokes the RAG chain with a question and returns the answer and sources.

    Args:
        rag_chain: The configured RAG chain runnable.
        question: The user's question.

    Returns:
        A tuple containing (answer_text, source_documents).
    """
    print(f"Invoking RAG chain with question: '{question[:50]}...'")
    if rag_chain is None:
        return "Error: RAG chain is not initialized.", []

    try:
        # Pass the question as a dictionary, matching the expected input structure for the final_chain
        result = rag_chain.invoke({"question": question})

        answer = result.get("answer", "Sorry, I couldn't generate an answer.")
        sources = result.get("sources", [])

        print(f"RAG chain finished. Answer length: {len(answer)}, Sources found: {len(sources)}")
        return answer, sources

    except Exception as e:
        print(f"Error invoking RAG chain: {e}")
        import traceback
        traceback.print_exc()
        return f"Sorry, an error occurred while processing your question: {e}", []
