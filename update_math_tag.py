import pymongo
from dotenv import load_dotenv
import os
load_dotenv()
from dotenv import load_dotenv
from latext import latex_to_text
from utils import timeit
import requests
import uuid

@timeit
def latext_to_text_to_speech(text):
    # Remove leading backslashes and add dollar signs at the beginning and end of the text
    text = "${}$".format(text)
    # Convert the LaTeX text to text to speech
    text_to_speech = latex_to_text(text)
    return text_to_speech


client= pymongo.MongoClient(os.environ['DATABASE_URL'])
db= client.epub_testing
oct_chapters=db.oct_chapters

# documents = oct_chapters.find({"book": "Essential Math for Data Science (9781098102920)"})


# for document in documents:
#     # Keep track of modified sections
#     modified_sections = []

#     # Iterate over sections
#     for section in document.get("sections", []):
#         section_id = section.get("_id") or str(uuid.uuid4())  # Use existing ID or generate a new UUID
#         equations = section.get("equations", [])

#         # Check if section has equations
#         if equations:
#             # Iterate over equations
#             for equation in equations:
#                 math_tag = equation.get("math_tag")
#                 api_url = 'http://localhost:9000'
#                 # Check if equations array is not empty and math_tag exists
#                 if equations and math_tag:
#                     # Call your API to get LaTeX code
#                     response = requests.post(api_url, json={"math_tag": math_tag})

#                     if response.status_code == 200:
#                         latex_code = response.json().get("data")

#                         # Update equation object
#                         equation["text"] = latex_code
#                         text_to_speech = latext_to_text_to_speech(latex_code)
#                         equation["text_to_speech"] = text_to_speech
#                         equation.pop("math_tag", None)

#             # Update only the "equations" array within the section
#             section["equations"] = equations

#         # Add modified section to the list
#         modified_sections.append(section)

#     # Update MongoDB with modified sections
#     oct_chapters.update_one(
#         {"_id": document["_id"]},
#         {"$set": {"sections": modified_sections}}
#     )





# Find all documents in oct_chapters
all_documents = oct_chapters.find({})

for document in all_documents:
    # Keep track of modified sections
    modified_sections = []

    # Iterate over sections
    for section in document.get("sections", []):
        section_id = section.get("_id") or str(uuid.uuid4())  # Use existing ID or generate a new UUID
        equations = section.get("equations", [])

        # Check if section has equations
        if equations:
            # Iterate over equations
            for equation in equations:
                math_tag = equation.get("math_tag")
                api_url = 'http://localhost:9001'
                # Check if equations array is not empty and math_tag exists
                if equations and math_tag:
                    # Call your API to get LaTeX code
                    response = requests.post(api_url, json={"math_tag": math_tag})

                    if response.status_code == 200:
                        latex_code = response.json().get("data")

                        # Update equation object
                        equation["text"] = latex_code
                        text_to_speech = latext_to_text_to_speech(latex_code)
                        equation["text_to_speech"] = text_to_speech
                        equation.pop("math_tag", None)

            # Update only the "equations" array within the section
            section["equations"] = equations

        # Add modified section to the list
        modified_sections.append(section)

    # Update MongoDB with modified sections
    oct_chapters.update_one(
        {"_id": document["_id"]},
        {"$set": {"sections": modified_sections}}
    )