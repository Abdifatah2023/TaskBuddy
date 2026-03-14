import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("CANVAS_API_TOKEN")
BASE_URL = os.getenv("CANVAS_BASE_URL")

headers = {
    "Authorization": f"Bearer {TOKEN}"
}

# This returns all the courses that the token has access to
# response = requests.get(f"{BASE_URL}/api/v1/courses", headers=headers)

course_id = 1908208
# Gets assignments for our Agentic AI course
# response = requests.get(
#     f"{BASE_URL}/api/v1/courses/{course_id}/assignments",
#     headers=headers
# )

# Gets the syllabus of the Agentic AI course
# response = requests.get(
#     f"{BASE_URL}/api/v1/courses/{course_id}?include[]=syllabus_body",
#     headers=headers
# )

# data = response.json()
# print(data["syllabus_body"])


# Get modules
# response = requests.get(
#     f"{BASE_URL}/api/v1/courses/{course_id}/modules",
#     headers=headers
# )

# Get modules
# response = requests.get(
#     f"{BASE_URL}/api/v1/courses/{course_id}/modules",
#     headers=headers
# )

# Get modules Unit 3
module_id = 4463504
response = requests.get(
    f"{BASE_URL}/api/v1/courses/{course_id}/modules/{module_id}/items",
    headers=headers
)

print(response.json())