# Imports
import os
from dotenv import load_dotenv

from langchain_text_splitters import RecursiveCharacterTextSplitter
# from langchain_community.document_loaders import DirectoryLoader, UnstructuredFileLoader
from langchain_community.vectorstores import Chroma
# from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_core.documents import Document

# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Initialize the loader with the path to the directory which contains the pdfs
# loader = DirectoryLoader(
#     "canvas_course_content", # Folder where the course content was uploaded by the canvas_api.py script
#     glob="**/*.txt",         # Get txt files from all folders
#     loader_cls=TextLoader,
# )


documents = []

base_dir = "canvas_course_content"

for root, _, files in os.walk(base_dir):
    for file in files:
        if not file.endswith(".txt"):
            continue

        path = os.path.join(root, file)

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()

            if len(text.strip()) < 50:
                continue  # skip empty/useless files

            documents.append(
                Document(
                    page_content=text,
                    metadata={"source": path}
                )
            )

        except Exception as e:
            print(f"❌ Skipping {path}: {e}")

print(f"Loaded {len(documents)} documents")



# Format of each document
# Document(page_content="...", metadata={"source": "Module 1/Intro.txt"})
# docs = loader.load()

# Handling files that may be almost empty
# docs = [doc for doc in docs if len(doc.page_content.strip()) > 50]

# Join all the content of all the pages
# full_text = "\n\n".join([doc.page_content for doc in docs])

# Add metadata (useful for filtering and citations)
# for doc in docs:
#     path = doc.metadata["source"]
    
#     parts = path.split("/")
#     if len(parts) > 1:
#         doc.metadata["module"] = parts[-2]
    
#     doc.metadata["type"] = "canvas_page"

parts = path.split(os.sep)

if len(parts) > 1:
    module_name = parts[-2]
else:
    module_name = "unknown"

documents.append(
    Document(
        page_content=text,
        metadata={
            "source": path,
            "module": module_name,
            "type": "canvas_page"
        }
    )
)

# create chunks
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=500,
    # chunk_size = 800,
    # chunk_overlap = 150,
)
# chunks = text_splitter.split_text(full_text)
chunks = text_splitter.split_documents(documents)

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
