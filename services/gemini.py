import os
import google.generativeai as genai
from dotenv import load_dotenv
import logging
import time

# Load environment variables
load_dotenv()

# Configure Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY is not set in environment variables or .env file")
    raise ValueError("GEMINI_API_KEY environment variable is required")

genai.configure(api_key=GEMINI_API_KEY)

def call_gemini(prompt: str, system_message: str, history: list, max_retries: int = 3, retry_delay: float = 2.0):
    """
    Generate a dialogue using the Gemini API with error handling and retries.
    
    Args:
        prompt: The prompt to send to Gemini
        system_message: System instruction for the model
        history: Chat history for context
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries
        
    Returns:
        Generated text response
    """
    generation_config = {
        "temperature": 0.8,
        "top_p": 0.9,
        "top_k": 40,
        "max_output_tokens": 8192,
    }
        
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config=generation_config,
        system_instruction=system_message
    )
    
    retry_count = 0
    last_error = None
    
    while retry_count < max_retries:
        try:
            chat_session = model.start_chat(history=history)
            response = chat_session.send_message(prompt)
            
            if response.text:
                return response.text
            else:
                raise ValueError("Empty response from Gemini API")
                
        except Exception as e:
            last_error = e
            retry_count += 1
            
            if "quota" in str(e).lower() or "rate" in str(e).lower():
                wait_time = retry_delay * (2 ** retry_count)
                logging.warning(f"Rate limit or quota exceeded. Waiting {wait_time:.2f}s before retry {retry_count}/{max_retries}")
                time.sleep(wait_time)
            elif retry_count < max_retries:
                logging.warning(f"Error with Gemini API: {e}. Retrying {retry_count}/{max_retries}...")
                time.sleep(retry_delay)
            else:
                break
    
    # If all retries failed
    logging.error(f"Failed to get response from Gemini after {max_retries} retries. Last error: {last_error}")
    raise last_error or Exception("Failed to generate response from Gemini API")

def upload_to_gemini(path, mime_type=None):
    """
    Upload a file to Gemini for processing.
    
    Args:
        path: Path to the file to upload
        mime_type: MIME type of the file
        
    Returns:
        Uploaded file object
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
        
    try:
        file = genai.upload_file(path, mime_type=mime_type)
        logging.info(f"Uploaded file '{file.display_name}' as: {file.uri}")
        return file
    except Exception as e:
        logging.error(f"Failed to upload file to Gemini: {e}")
        raise ValueError(f"Failed to upload file to Gemini: {e}")