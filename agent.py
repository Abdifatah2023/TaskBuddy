import os
from dotenv import load_dotenv


from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain.agents import create_agent
from google_calendar import GoogleCalendarTool
from email_alerts import authenticate, get_weekly_events, format_event, gmail_send_message
from rag_chain import rag_chain

# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')


# rag chaing custom tool
@tool
def extract_assignments(query: str) -> str:
    """
    Extract all assignments or events with deadlines from the syllabus documents.
    """
    return rag_chain.invoke(query)


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
    
@tool
def extract_weekly_deadlines(query: str) -> str:
    """
    Extract upcoming deadlines (next 7 days) from syllabus using RAG.
    """
    print("Running extract_weekly_deadlines...")
    return rag_chain.invoke(query)


@tool
def send_weekly_calendar_bulletin(_: str = "send") -> str:
    """
    Build and send a weekly bulletin from Google Calendar events using email_alerts.py.
    """
    try:
        print("Running send_weekly_calendar_bulletin...")
        creds = authenticate()
        if not creds:
            return "Failed: could not authenticate."

        events = get_weekly_events(creds)
        if events is None:
            return "Failed: could not fetch calendar events."

        email_body = format_event(events)
        result = gmail_send_message(creds, email_body)

        if result is None:
            return "Failed: Gmail send returned no result."
        print("Success: weekly bulletin email sent.")
        return "Success: weekly bulletin email sent."
    except Exception as e:
        return f"Failed with error: {str(e)}"

@ tool 
def study_plan(topic: str) -> str : 
    """
    This tool gets any topic and then uses the documents in the RAG pipeline to break down the topic
    into small sections and subtopics. This can be used by the agent to break down a task and create a
    study schedule for the user.
    """
    result = rag_chain.invoke(f'I need to study for an exam on {topic}. Can you break it apart into smaller sections or subtopics big enough to study in a day?')
    return result


# Agent Setup

agent_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

system_prompt = """
    You are an academic assistant.

    You have the following tools:

    1. extract_assignments:
    - Use this FIRST to retrieve assignments and deadlines.
    - It returns structured JSON.

    2. create_calendar_event:
    - Use this to create Google Calendar events.
    - Only create events when due_date is NOT null.

    3. extract_weekly_deadlines:
    - Extracts all the deadlines for the next seven days.
    - After this tool, you must use send_weekly_calendar_bulletin to send out an email.

    4. send_weekly_calendar_bulletin
    - follows up extract_weekly_deadlines.
    - sends an email to the recipient of the upcoming deadlines.

    5. study_plan
    - takes in a topic as an input.
    - returns a breakdown of sections or subtopics of that topic that can be each completed in a day.


    Things you can do: 
    1) In order to map all deadlines and due dates for a semester in the user's calendar:
    Step 1: Call extract_assignments.
    Step 2: Parse JSON results.
    Step 3: For each assignment with a valid due_date, call create_calendar_event.
    2) Send a bulletin email reminder about upcoming deadlines
    Step 1: Extract upcoming deadlines for the next 7 days by using extract_weekly_deadlines.
    Step 2: Use send_weekly_calendar_bulletin to send a bulletin email of upcoming deadlines to the recipient.
    Step 3: Summarize what was created.
    3) Create a study plan for a user
    Step 1: Use the study plan tool to get a breakdown of a topic into subtopics that can each be studies in a day
    Step 2: Assign dates to each sub topic.
    Step 3 : Map these dates into the following JSON format
    [
        {{
            "assignment_name": "Homework 1",
            "due_date": "2026-03-10"
        }}
    ]
    Step 4: Add these dates to the google calendar along with the subtopic name as the title

    Do NOT hallucinate assignments.

    """

agent = create_agent(
    model=agent_llm,
    tools=[extract_assignments, create_calendar_event, extract_weekly_deadlines, send_weekly_calendar_bulletin, study_plan],
    system_prompt=system_prompt
)

print("Agent ready with 5 tools!")
print("="*50)





# Invoke the agent

# response = agent.invoke({
#     "messages": [
#         {
#             "role": "human",
#             "content": "Extract all assignments and create calendar events for them. Then extract the upcoming deadlines for the next 7 days and send out the email"
#         }
#     ]
# })

response = agent.invoke({
    "messages": [
        {
            "role": "human",
            "content": "Create a study plan for the topic 'perceptrons' and add the dates to my calendar. I would like to start studying on June 1st, 2026"
        }
    ]
})

# response = agent.invoke({
#     "messages": [
#         {
#             "role": "human",
#             "content": "Extract all the deadlines in the next month and send me a bulletin to my email"
#         }
#     ]
# })



result = response["messages"][-1].content
print(result)
