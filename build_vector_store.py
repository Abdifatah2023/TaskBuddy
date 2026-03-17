# Imports
import os
from dotenv import load_dotenv

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import Chroma
# from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Mock Data path
mock_data_path = r"V:\OneDrive\Shreena Documents\GitHub\TaskBuddy\Syllabi"

# Initialize the loader with the path to the directory which contains the pdfs
loader = DirectoryLoader(
    mock_data_path,
    glob="*.pdf",
    loader_cls=PyPDFLoader
)

docs = loader.load()

# Join all the content of all the pages
# full_text = "\n\n".join([doc.page_content for doc in docs])

# create chunks
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=500,
    # chunk_size = 800,
    # chunk_overlap = 150,
)
# chunks = text_splitter.split_text(full_text)
chunks = text_splitter.split_documents(docs)

# embedding
embeddings = GoogleGenerativeAIEmbeddings(
    model = "models/gemini-embedding-001"
)

# Create a Chroma vector store from the chunks
# vectorstore = Chroma.from_texts(
#     texts=chunks,
#     embedding=embeddings,
#     collection_name="TaskBudy",
#     persist_directory="./vector_db"
# )
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    collection_name="TaskBudy",
    persist_directory="./vector_db"
)

vectorstore.persist()
# Run once using : 
# python build_vector_store.py

# This is the indexing phase
# 1. Load documents
# 2. Chunk them
# 3. Create embeddings
# 4. Store them in a vector database
# - Only run once or when the documents change

# Query phase runs every time a user asks a question. This will be in agent.py
# 1.Take user query
# 2. Embed query
# 3. Search vector store
# 4. Retrieve relevant chunks
# 5. Send chunks + question to LLM
