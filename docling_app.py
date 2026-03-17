from langchain_docling import DoclingLoader
from docling.document_converter import DocumentConverter
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma
from dotenv import load_dotenv
import os


file = r"C:\Users\abdif\OneDrive\Documents\TaskBuddy\Syllabi\Syllabus.pdf"

loader = DoclingLoader(file_path=file)

docs = loader.load()
print(f"Loaded {len(docs)} documents from DoclingLoader")



converter = DocumentConverter()
result = converter.convert_single(file)
print(result.render_as_markdown()) 



# for doc in docs:
#     print(doc.page_content, "\n---\n")







# load_dotenv()


# embeddings = GoogleGenerativeAIEmbeddings(
#     model = "models/gemini-embedding-001",
# )
# vectorstore = Chroma.from_texts(
#     texts=docs,
#     embedding=embeddings,
#     collection_name="taskbuddy_with_docling"
# )

# print(f"Stored {vectorstore._collection.count()} chunks in the vector store")


