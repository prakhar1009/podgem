import os
import time
import logging
from dotenv import load_dotenv
import requests
from typing import List, Literal, Optional
import concurrent.futures as cf

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# Validate API key is available
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVENLABS_API_KEY:
    logging.warning("ELEVENLABS_API_KEY is not set in environment variables or .env file")

class DialogueItem:
    def __init__(self, text: str, speaker: Literal["male-1", "female-1"]):
        self.text = text
        self.speaker = speaker

    @property
    def voice_id(self):
        voice_mapping = {
            "male-1": "UgBBYS2sOqTuMpoF3BR0",
            "female-1": "KYiVPerWcenyBTIvWbfY"
        }
        
        if self.speaker not in voice_mapping:
            logging.error(f"Unknown speaker type: {self.speaker}. Using default voice.")
            return voice_mapping["male-1"]
            
        return voice_mapping[self.speaker]

def check_api_key() -> bool:
    """Verify if the ElevenLabs API key is valid."""
    if not ELEVENLABS_API_KEY:
        logging.error("ElevenLabs API key is not set. Please set the ELEVENLABS_API_KEY environment variable.")
        return False
    return True

def get_elevenlabs_audio(text: str, voice_id: str, max_retries: int = 3, retry_delay: float = 2.0) -> bytes:
    """
    Convert text to speech using ElevenLabs API with retry mechanism and better error handling.
    
    Args:
        text: The text to convert to speech
        voice_id: The ElevenLabs voice ID
        max_retries: Maximum number of retry attempts for rate limiting or temporary issues
        retry_delay: Delay in seconds between retry attempts
        
    Returns:
        Bytes of audio data
        
    Raises:
        ValueError: For API key, rate limit, or voice ID issues
        requests.exceptions.RequestException: For network or API errors
    """
    if not check_api_key():
        raise ValueError("ElevenLabs API key is not set or is invalid")
        
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    # Truncate very long text if needed (ElevenLabs has character limits)
    if len(text) > 5000:
        logging.warning(f"Text too long ({len(text)} chars), truncating to 5000 chars")
        text = text[:4997] + "..."
        
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    retry_count = 0
    last_error = None

    while retry_count < max_retries:
        try:
            response = requests.post(url, json=payload, headers=headers, stream=True)
            
            # Handle different error cases
            if response.status_code == 401:
                logging.error("Authentication failed: ElevenLabs API key is invalid or expired")
                raise ValueError("ElevenLabs API key is invalid or expired. Please check your API key.")
                
            elif response.status_code == 429:
                retry_count += 1
                wait_time = retry_delay * (2 ** retry_count)  # Exponential backoff
                logging.warning(f"Rate limit exceeded. Waiting {wait_time:.2f}s before retry {retry_count}/{max_retries}")
                time.sleep(wait_time)
                continue
                
            elif response.status_code == 400:
                try:
                    error_data = response.json()
                    error_message = error_data.get("detail", {}).get("message", "Unknown validation error")
                except:
                    error_message = "Invalid request parameters"
                logging.error(f"ElevenLabs API validation error: {error_message}")
                raise ValueError(f"ElevenLabs API validation error: {error_message}")
                
            elif response.status_code == 404:
                logging.error(f"Voice ID not found: {voice_id}")
                raise ValueError(f"Voice ID not found: {voice_id}. Please check your voice configuration.")
            
            # For other errors
            response.raise_for_status()
            return response.content
            
        except requests.exceptions.HTTPError as http_err:
            last_error = http_err
            if response.status_code != 429:  # Don't retry for non-rate-limit errors
                break
                
        except requests.exceptions.ConnectionError as conn_err:
            last_error = conn_err
            retry_count += 1
            wait_time = retry_delay * (2 ** retry_count)
            logging.warning(f"Connection error. Waiting {wait_time:.2f}s before retry {retry_count}/{max_retries}")
            time.sleep(wait_time)
            
        except Exception as err:
            logging.error(f"Unexpected error with ElevenLabs API: {err}")
            raise err
    
    # If we've exhausted retries or had a non-retryable error
    if retry_count >= max_retries:
        raise ValueError("ElevenLabs API rate limit exceeded and maximum retries reached. Try again later.")
    elif last_error:
        raise last_error
    else:
        raise ValueError("Failed to generate audio with ElevenLabs API")

def generate_audio(dialogue_items: List[DialogueItem], max_chunk_size: int = 5, output_filename: str = "podcast.mp3") -> dict:
    """
    Generate audio from dialogue items with better error handling and rate limiting.
    
    Args:
        dialogue_items: List of DialogueItem objects to convert to audio
        max_chunk_size: Maximum number of items to process in parallel to avoid rate limiting
        output_filename: Name of the output audio file
        
    Returns:
        Dict with audio_path, transcript_path, and other metadata
    """
    if not dialogue_items:
        raise ValueError("No dialogue items provided")
        
    audio = b""
    transcript = ""
    
    # Verify API key before starting
    if not check_api_key():
        raise ValueError("Cannot generate audio: ElevenLabs API key is not set")
        
    logging.info(f"Starting audio generation for {len(dialogue_items)} dialogue items")
    
    # Process dialogues in smaller chunks to avoid rate limits
    total_processed = 0
    for i in range(0, len(dialogue_items), max_chunk_size):
        chunk = dialogue_items[i:i+max_chunk_size]
        
        with cf.ThreadPoolExecutor(max_workers=2) as executor:  # Limit concurrent requests
            futures = []
            for line in chunk:
                transcript_line = f"{line.speaker}: {line.text}"
                future = executor.submit(get_elevenlabs_audio, line.text, line.voice_id)
                futures.append((future, transcript_line))

            for future, transcript_line in futures:
                try:
                    audio_chunk = future.result(timeout=30)  # Add timeout
                    audio += audio_chunk
                    transcript += transcript_line + "\n\n"
                    total_processed += 1
                    logging.info(f"Generated audio for dialogue {total_processed}/{len(dialogue_items)}")
                    # Small delay between chunks to avoid hitting rate limits
                    time.sleep(0.3)
                except Exception as e:
                    logging.error(f"Error generating audio for line: {transcript_line}\nError: {str(e)}")
                    # Add error note to transcript but continue processing
                    transcript += f"[ERROR generating audio for: {transcript_line}]\n\n"
        
        # Add delay between chunks to avoid rate limits
        if i + max_chunk_size < len(dialogue_items):
            logging.info(f"Processed {i + len(chunk)}/{len(dialogue_items)} dialogue items. Pausing to avoid rate limits...")
            time.sleep(2)

    # Save audio to file
    if audio:
        try:
            with open(output_filename, "wb") as f:
                f.write(audio)
            logging.info(f"Audio saved to {output_filename}")
            audio_path = output_filename
        except Exception as e:
            logging.error(f"Failed to save audio file: {e}")
            raise ValueError(f"Failed to save audio file: {e}")
    else:
        raise ValueError("No audio was generated")
    
    # Save transcript to file
    transcript_filename = "podcast_transcript.txt"
    try:
        with open(transcript_filename, "w", encoding="utf-8") as f:
            f.write(transcript)
        logging.info(f"Transcript saved to {transcript_filename}")
    except Exception as e:
        logging.error(f"Failed to save transcript file: {e}")
        transcript_filename = None

    return {
        "audio_path": audio_path,
        "transcript_path": transcript_filename,
        "total_items": len(dialogue_items),
        "processed_items": total_processed,
        "audio_duration_estimate": f"{len(dialogue_items) * 5}s",  # Rough estimate
        "file_size": f"{len(audio) / 1024 / 1024:.2f} MB"
    }