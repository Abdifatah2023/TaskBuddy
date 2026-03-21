import os
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

TOKEN = os.getenv("CANVAS_API_TOKEN")
BASE_URL = os.getenv("CANVAS_BASE_URL")
COURSE_ID = os.getenv("COURSE_ID")

headers = {
    "Authorization": f"Bearer {TOKEN}"
}

# ------------------------
# Helper Functions
# ------------------------

def clean_filename(name):
    return "".join(c for c in name if c.isalnum() or c in "._- ").strip()

def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    return text.encode("utf-8", "ignore").decode("utf-8") # ensures that all files are valid UTF-8 for the loader in RAG

def get_paginated(url):
    results = []
    while url:
        res = requests.get(url, headers=headers)
        data = res.json()
        results.extend(data)
        url = res.links.get("next", {}).get("url")
    return results

# ------------------------
# Setup folders
# ------------------------

base_dir = "canvas_course_content"
os.makedirs(base_dir, exist_ok=True)

# ------------------------
# 1. Get Syllabus
# ------------------------

print("Fetching syllabus...")

syllabus_url = f"{BASE_URL}/api/v1/courses/{COURSE_ID}?include[]=syllabus_body"
res = requests.get(syllabus_url, headers=headers)
course_data = res.json()

syllabus_html = course_data.get("syllabus_body", "")
syllabus_text = html_to_text(syllabus_html)

with open(os.path.join(base_dir, "syllabus.txt"), "w", encoding="utf-8") as f:
    f.write(syllabus_text)

# ------------------------
# 2. Get Modules
# ------------------------

print("Fetching modules...")

modules_url = f"{BASE_URL}/api/v1/courses/{COURSE_ID}/modules"
modules = get_paginated(modules_url)

for module in modules:
    module_name = clean_filename(module["name"])
    module_id = module["id"]

    print(f"\nProcessing module: {module_name}")

    module_dir = os.path.join(base_dir, module_name)
    os.makedirs(module_dir, exist_ok=True)

    # ------------------------
    # 3. Get Module Items
    # ------------------------

    items_url = f"{BASE_URL}/api/v1/courses/{COURSE_ID}/modules/{module_id}/items"
    items = get_paginated(items_url)

    for item in items:
        item_type = item["type"]
        item_title = clean_filename(item["title"])

        print(f"  - {item_type}: {item_title}")

        # ------------------------
        # Handle Pages
        # ------------------------
        if item_type == "Page":
            page_url = item["url"]  # API URL for page

            res = requests.get(page_url, headers=headers)
            page_data = res.json()

            html = page_data.get("body", "")
            text = html_to_text(html)

            with open(
                os.path.join(module_dir, f"{item_title}.txt"),
                "w",
                encoding="utf-8"
            ) as f:
                f.write(text)

        # ------------------------
        # Handle Files
        # ------------------------
        elif item_type == "File":
            file_url = item["url"]

            res = requests.get(file_url, headers=headers)
            file_data = res.json()

            download_url = file_data["url"]
            filename = clean_filename(file_data["filename"])

            file_content = requests.get(download_url, headers=headers)

            with open(
                os.path.join(module_dir, filename),
                "wb"
            ) as f:
                f.write(file_content.content)

print("\nDone! All content downloaded and extracted.")











# This returns all the courses that the token has access to
# response = requests.get(f"{BASE_URL}/api/v1/courses", headers=headers)


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
# module_id = 4463504
# response = requests.get(
#     f"{BASE_URL}/api/v1/courses/{course_id}/modules/{module_id}/items",
#     headers=headers
# )

# print(response.json())