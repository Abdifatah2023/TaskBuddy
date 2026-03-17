import os
from dotenv import load_dotenv

from langchain_community.vectorstores import Chroma
# from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# embedding (for embedding the query)
embeddings = GoogleGenerativeAIEmbeddings(
    model = "models/gemini-embedding-001"
)

# load the vector store
vectorstore = Chroma(
    persist_directory="./vector_db",
    embedding_function=embeddings,
    collection_name="TaskBudy"
)

# create retriever
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

# test retriever
# docs = retriever.invoke("syllabi")
# print("Retrieved docs:", len(docs))

# for d in docs:
#     print(d.page_content[:200])

# prompt template
template = """You are a helpful academic assistant. You can do two main things: 
            1) You can use the syllabus document to extract every assignment, project, or exam mentioned and then 
                give the due dates for each. Use the given context to find these.  
                Return output STRICTLY in JSON format like this:
                [
                {{
                    "assignment_name": "Homework 1",
                    "due_date": "2026-03-10"
                }}
                ]
                If a due date is missing, set "due_date" to null
            2) You can use notes, syllabus and all other documents to give an answer to the question or a summary (if asked for)
                which would be grounded in the context given below. 
                The information isn't provided in the context, say 
                "I don't  have enough information to answer the question."

            Context: 
            {context}

            Question: 
            {question}

            """


prompt = ChatPromptTemplate.from_template(template)

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

# rag chain pipeline
rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | ChatGoogleGenerativeAI(model="gemini-flash-latest")
    | StrOutputParser()
)
# query = "Could give me a table of all the assignment and events along with their deadlines from the Web Technologies class syllabus files?"
# print(rag_chain.invoke(query))

# query = "Can you explain what the average perceptron and voted perceptron are and how they differ?"
# print(rag_chain.invoke(query))

# query = "I need to study for a perceptron exam. Can you break it apart into topics"
# print(rag_chain.invoke(query))

# Check retrieved chunks
# query = "Get me something from your context"
# results = vectorstore.similarity_search(query, k = 1)
# print("Retrieved Chunks : ")
# for i in results:
#   print(i.page_content)
# print()