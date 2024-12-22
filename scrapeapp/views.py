from django.shortcuts import render

from django.shortcuts import render

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import os
import random
import time
import json
from typing import List, Type
from bs4 import BeautifulSoup
import html2text
from pydantic import BaseModel, create_model
from groq import Groq
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from assets import USER_AGENTS, HEADLESS_OPTIONS, USER_MESSAGE, GROQ_LLAMA_MODEL_FULLNAME

# Initialize dotenv if needed
from dotenv import load_dotenv
load_dotenv()

# Set up the Chrome WebDriver options (similar to your existing setup)
def setup_selenium():
    import tempfile
    os.environ["WDM_LOCAL"] = tempfile.mkdtemp()  # Use a writable temporary directory

    options = Options()
    user_agent = random.choice(USER_AGENTS)
    options.add_argument(f"user-agent={user_agent}")

    for option in HEADLESS_OPTIONS:
        options.add_argument(option)

    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def click_accept_cookies(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//button | //a | //div"))
        )
        
        accept_text_variations = [
            "accept", "agree", "allow", "consent", "continue", "ok", "I agree", "got it"
        ]
        
        for tag in ["button", "a", "div"]:
            for text in accept_text_variations:
                try:
                    element = driver.find_element(By.XPATH, f"//{tag}[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]")
                    if element:
                        element.click()
                        print(f"Clicked the '{text}' button.")
                        return
                except:
                    continue

        print("No 'Accept Cookies' button found.")
    
    except Exception as e:
        print(f"Error finding 'Accept Cookies' button: {e}")


def fetch_html_selenium(url):
    driver = setup_selenium()
    try:
        driver.get(url)
        time.sleep(1)
        driver.maximize_window()
        
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        html = driver.page_source
        return html
    finally:
        driver.quit()


def clean_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    for element in soup.find_all(['header', 'footer']):
        element.decompose()
    return str(soup)


def html_to_markdown_with_readability(html_content):
    cleaned_html = clean_html(html_content)
    markdown_converter = html2text.HTML2Text()
    markdown_converter.ignore_links = False
    return markdown_converter.handle(cleaned_html)


def create_dynamic_listing_model(field_names: List[str]) -> Type[BaseModel]:
    field_definitions = {field: (str, ...) for field in field_names}
    return create_model('DynamicListingModel', **field_definitions)


def create_listings_container_model(listing_model: Type[BaseModel]) -> Type[BaseModel]:
    return create_model('DynamicListingsContainer', listings=(List[listing_model], ...))


def generate_system_message(listing_model: BaseModel) -> str:
    schema_info = listing_model.model_json_schema()

    field_descriptions = []
    for field_name, field_info in schema_info["properties"].items():
        field_type = field_info["type"]
        field_descriptions.append(f'"{field_name}": "{field_type}"')

    schema_structure = ",\n".join(field_descriptions)

    system_message = f"""
    You are an intelligent text extraction and conversion assistant. Your task is to extract structured information 
    from the given text and convert it into a pure JSON format. The JSON should contain only the structured data extracted from the text, 
    with no additional commentary, explanations, or extraneous information. When the text has ellipsis, find the full text.The locations of the events should be in Kenya only.
    Make sure the photo links are valid.
    You could encounter cases where you can't find the data of the fields you have to extract or the data will be in a foreign language.
    Please process the following text and provide the output in pure JSON format with no words before or after the JSON:
    Please ensure the output strictly follows this schema:

    {{
        "listings": [
            {{
                {schema_structure}
            }}
        ]
    }} """

    return system_message

def format_data(data, DynamicListingsContainer, DynamicListingModel, selected_model):
    token_counts = {}
        
    sys_message = generate_system_message(DynamicListingModel)

    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": sys_message},
            {"role": "user", "content": USER_MESSAGE + data}
        ],
        model=GROQ_LLAMA_MODEL_FULLNAME
    )

    response_content = completion.choices[0].message.content
    parsed_response = json.loads(response_content)
    
    token_counts = {
        "input_tokens": completion.usage.prompt_tokens,
        "output_tokens": completion.usage.completion_tokens
    }

    return parsed_response, token_counts


@csrf_exempt
def api_view(request):
    if request.method == "GET":
        url = 'https://www.ticketsasa.com/events/listing/upcoming'
        fields = ['Name', 'Price', 'Location', 'Time', 'Photos']
        
        try:
            # Step 1: Scrape data
            raw_html = fetch_html_selenium(url)

            # Step 2: Convert to markdown with better readability
            markdown = html_to_markdown_with_readability(raw_html)

            # Step 3: Create dynamic models
            DynamicListingModel = create_dynamic_listing_model(fields)
            DynamicListingsContainer = create_listings_container_model(DynamicListingModel)

            # Step 4: Format the scraped data
            formatted_data, token_counts = format_data(
                markdown,
                DynamicListingsContainer,
                DynamicListingModel,
                "Groq Llama3.1 70b"
            )

            # Return the formatted data as JSON
            response_data = {
                'formatted_data': formatted_data,
                'token_counts': token_counts,
            }
            return JsonResponse(response_data, status=200)

        except Exception as e:
            error_message = {"error": str(e)}
            return JsonResponse(error_message, status=500)
