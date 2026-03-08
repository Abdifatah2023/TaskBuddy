import os
from dotenv import load_dotenv

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.tools import tool
from langchain.agents import create_agent

from Google_calendar import GoogleCalendarTool


# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# 2. Define your Mock Data path (Update 'MyProjectFolder' to your actual folder name)
mock_data_path = r"C:\Users\Owner\Documents\TaskBuddy\Syllabi\Syllabus2.pdf"


# Initialize the loader with the path to your PDF file
loader = PyPDFLoader(mock_data_path)

# Load the documents (each page is a separate Document object)
pages = loader.load()


full_text = "\n\n".join([page.page_content for page in pages])


# create chunks
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=500,
)
chunks = text_splitter.split_text(full_text)


# embedding
embeddings = GoogleGenerativeAIEmbeddings(
    model = "models/gemini-embedding-001"
)

# Create a Chroma vector store from the chunks
vectorstore = Chroma.from_texts(
    texts=chunks,
    embedding=embeddings,
    collection_name="TaskBudy"
)


  # --- Prompt and Generation ---
template = """You are an academic assistant. 
            Extract every assignment, project, or exam mentioned.

            Return output STRICTLY in JSON format like this:

            [
            {{
                "assignment_name": "Homework 1",
                "due_date": "2026-03-10"
            }}
            ]

            If a due date is missing, set "due_date" to null

            Context: 
            {context}

            Question: 
            {question}

            """

prompt = ChatPromptTemplate.from_template(template)

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


base_rag_chain = (
    {
        "context": vectorstore.as_retriever(search_kwargs={"k": 5}) | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | ChatGoogleGenerativeAI(model="gemini-flash-latest")
    | StrOutputParser()
)



# rag chaing custom tool
@tool
def extract_assignments(query: str) -> str:
    """
    Extract assignments and deadlines from syllabus documents.
    """
    return base_rag_chain.invoke(query)


# Google calendar custom tool
@tool
def create_calendar_event(
    title: str,
    due_date: str 
) -> str:
    
    """
    Create a Google Calendar event for an assignment. 
    The event should start and end on the due date.
    """

    GoogleCalendarTool(
        title=title, 
        start_time=due_date, 
        end_time=due_date
    )
    return f"Event created for {title} on {due_date}"
    



# Agent Setup

agent_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

system_prompt = """
    You are an academic assistant.

    You have two tools:

    1. extract_assignments:
    - Use this FIRST to retrieve assignments and deadlines.
    - It returns structured JSON.

    2. create_calendar_event:
    - Use this to create Google Calendar events.
    - Only create events when due_date is NOT null.

    Workflow:
    Step 1: Call extract_assignments.
    Step 2: Parse JSON results.
    Step 3: For each assignment with a valid due_date, call create_calendar_event.
    Step 4: Summarize what was created.

    Do NOT hallucinate assignments.

    """

agent = create_agent(
    model=agent_llm,
    tools=[extract_assignments, create_calendar_event],
    system_prompt=system_prompt
)

print("Agent ready with 2 tools!")
print("="*50)





# Invoke the agent
response = agent.invoke({
    "messages": [
        {
            "role": "human",
            "content": "Extract all assignments and create calendar events for them."
        }
    ]
})

result = response["messages"][-1].content
print(result)
