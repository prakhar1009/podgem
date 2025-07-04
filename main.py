import streamlit as st
from services.elevenlabs import *
from services.gemini import *
import os
import logging
import requests
from bs4 import BeautifulSoup
import trafilatura
import tiktoken
import json
from typing import Dict, List, Optional, Tuple, Union
import re
import time
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler("podgem.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("podgem")

# Token counter for rate limiting
def count_tokens(text: str, model: str = "gpt-3.5-turbo") -> int:
    """Count the number of tokens in a text string."""
    try:
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"Could not count tokens: {e}. Using character-based estimate.")
        # Fallback to character-based estimate (roughly 4 chars per token)
        return len(text) // 4

# Advanced web content extraction
def extract_website_content(url: str, max_tokens: int = 6000) -> Dict[str, str]:
    """Extract content from a website with advanced methods."""
    try:
        logger.info(f"Fetching content from {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        result = {
            "title": "",
            "description": "",
            "main_content": "",
            "meta": {}
        }
        
        soup = BeautifulSoup(response.text, 'html.parser')
        meta_desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
        if meta_desc and meta_desc.get('content'):
            result["description"] = meta_desc.get('content')
            
        main_content = trafilatura.extract(
            response.text,
            url=url,
            include_comments=False,
            include_tables=True,
            include_images=False,
            include_links=False,
            output_format="text"
        )
        
        if not main_content:
            paragraphs = soup.find_all('p')
            main_content = '\n\n'.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50)
        
        result["main_content"] = main_content
        title_tag = soup.find('title')
        result["title"] = title_tag.get_text(strip=True) if title_tag else ""
        
        content_text = f"Title: {result['title']}\n\nDescription: {result['description']}\n\n{result['main_content']}"
        token_count = count_tokens(content_text)
        
        logger.info(f"Extracted {token_count} tokens from {url}")
        
        if token_count > max_tokens:
            logger.info(f"Content exceeds {max_tokens} tokens, truncating...")
            header = f"Title: {result['title']}\n\nDescription: {result['description']}\n\n"
            header_tokens = count_tokens(header)
            remaining_tokens = max_tokens - header_tokens
            chars_to_keep = remaining_tokens * 4
            truncated_content = result["main_content"][:chars_to_keep]
            truncated_content += "\n\n[Content truncated due to length limits]"
            result["main_content"] = truncated_content
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to extract content from {url}: {e}", exc_info=True)
        return {"title": "", "description": "", "main_content": "", "meta": {}}

def summarize_content(content: str, target_length: str = "medium") -> str:
    """Use Gemini to summarize long content to a specified target length."""
    token_count = count_tokens(content)
    
    if token_count < 1000:
        logger.info(f"Content already short ({token_count} tokens), skipping summarization")
        return content
        
    length_settings = {
        "short": "a concise 2-3 paragraph summary",
        "medium": "a detailed 4-6 paragraph summary with key points",
        "long": "a comprehensive summary that preserves most important details and examples"
    }
    
    target = length_settings.get(target_length, length_settings["medium"])
    
    prompt = f"""
    Summarize the following content into {target}. Preserve the most important information, 
    key concepts, and any specific data or statistics that would be valuable in a podcast discussion.
    Focus on creating a coherent narrative that could be used as source material for a podcast.
    
    CONTENT TO SUMMARIZE:
    {content}
    """
    
    system_message = "You are an expert content summarizer who maintains the key information while reducing length."
    chat_history = []
    
    logger.info(f"Summarizing {token_count} tokens of content to '{target_length}' length")
    summary = call_gemini(prompt, system_message, chat_history)
    
    new_token_count = count_tokens(summary)
    logger.info(f"Summarization complete: {token_count} → {new_token_count} tokens")
    
    return summary

def extract_topics(content: str, num_topics: int = 5) -> List[str]:
    """Extract main topics from content."""
    prompt = f"""
    Identify the {num_topics} most important topics or themes in the following content.
    For each topic, provide a short phrase (3-5 words) that accurately describes it.
    Format your response as a simple list of topics, one per line.
    
    CONTENT:
    {content}
    """
    
    system_message = "You are an expert at identifying key topics and themes in content."
    chat_history = []
    
    response = call_gemini(prompt, system_message, chat_history)
    
    topics = []
    for line in response.strip().split('\n'):
        clean_line = re.sub(r'^\d+\.\s*|^-\s*|^•\s*', '', line).strip()
        if clean_line:
            topics.append(clean_line)
            
    return topics[:num_topics]

def get_company_info(company_name: str) -> str:
    """Get comprehensive information about a company using Gemini."""
    prompt = f"""
    Research and provide comprehensive information about the company '{company_name}'.
    
    Include the following information:
    1. Basic overview and history
    2. Main products or services offered
    3. Target market and customer base
    4. Recent news or developments (within the last 1-2 years)
    5. Competitive position in the industry
    6. Any interesting facts or notable aspects of the company culture
    
    Structure this information in a way that would be informative and engaging
    for a podcast audience who may not be familiar with the company.
    
    If you can find the company's official website, include that as well.
    """
    
    system_message = "You are an expert business researcher with extensive knowledge of companies across industries. Provide accurate, well-structured information suitable for a podcast script."
    chat_history = []
    
    logger.info(f"Researching company: {company_name}")
    company_info = call_gemini(prompt, system_message, chat_history)
    
    return company_info

def generate_podcast(prompt: str, system_message: str, content_source: str = "", source_type: str = "pdf") -> dict:
    """Generate a podcast from various content sources."""
    logger.info(f"Generating podcast from {source_type} source")
    
    try:
        chat_history = []
        
        # Handle different content source types
        if source_type == "pdf":
            if not os.path.isfile(content_source):
                raise FileNotFoundError(f"The file at path '{content_source}' does not exist.")
                
            files = upload_to_gemini(content_source, "application/pdf")
            chat_history = [{'role': 'user', 'parts': [{'file_data': {'mime_type': files.mime_type, 'file_uri': files.uri}}]}]
            
        elif source_type == "url":
            logger.info(f"Extracting content from URL: {content_source}")
            website_data = extract_website_content(content_source)
            
            if not website_data["main_content"].strip():
                raise ValueError("Could not extract meaningful content from the provided URL.")
                
            content_text = f"Title: {website_data['title']}\n\nDescription: {website_data['description']}\n\n{website_data['main_content']}"
            token_count = count_tokens(content_text)
            
            if token_count > 6000:
                logger.info(f"Website content is very long ({token_count} tokens), summarizing...")
                content_text = summarize_content(content_text, target_length="long")
                
            topics = extract_topics(content_text)
            topic_str = ", ".join(topics)
            
            context_message = f"WEBSITE: {website_data['title']}\n\nCONTENT SUMMARY:\n{content_text}\n\nMAIN TOPICS: {topic_str}"
            chat_history = [{'role': 'user', 'parts': [{'text': context_message}]}]
            
        elif source_type == "company":
            logger.info(f"Researching company: {content_source}")
            company_info = get_company_info(content_source)
            chat_history = [{'role': 'user', 'parts': [{'text': f"COMPANY INFORMATION:\n{company_info}"}]}]
            
        elif source_type == "text":
            token_count = count_tokens(content_source)
            text_content = content_source
            
            if token_count > 8000:
                logger.info(f"Raw text is very long ({token_count} tokens), summarizing...")
                text_content = summarize_content(content_source, target_length="long")
                
            chat_history = [{'role': 'user', 'parts': [{'text': text_content}]}]
            
        else:
            raise ValueError(f"Unsupported content source type: {source_type}")
        
        # Generate dialogue using Gemini
        logger.info("Generating podcast dialogue with Gemini...")
        dialogue = call_gemini(
            prompt=prompt,
            system_message=system_message,
            history=chat_history
        )
        
        # Parse dialogue into speaker parts
        dialogue_items = []
        for line in dialogue.split('\n'):
            line = line.strip()
            if not line:
                continue
                
            speaker_match = re.match(r'^(male-1|female-1|male|female|host|guest)\s*[:-]\s*(.+)$', line, re.IGNORECASE)
            
            if speaker_match:
                raw_speaker = speaker_match.group(1).lower()
                if raw_speaker in ["male", "host"]:
                    speaker = "male-1"
                elif raw_speaker in ["female", "guest"]:
                    speaker = "female-1"
                else:
                    speaker = raw_speaker
                    
                text = speaker_match.group(2).strip()
                dialogue_items.append(DialogueItem(text=text, speaker=speaker))
            else:
                # Alternate speakers if no explicit speaker
                if dialogue_items and line:
                    last_speaker = dialogue_items[-1].speaker
                    next_speaker = "female-1" if last_speaker == "male-1" else "male-1"
                    dialogue_items.append(DialogueItem(text=line, speaker=next_speaker))
        
        if not dialogue_items:
            raise ValueError("No valid dialogue items were parsed from the generated content")
        
        logger.info(f"Parsed {len(dialogue_items)} dialogue items")
        
        # Generate audio
        logger.info("Generating audio with ElevenLabs...")
        audio_result = generate_audio(dialogue_items, output_filename="podcast.mp3")
        
        return audio_result
        
    except Exception as e:
        logger.error(f"Error generating podcast: {e}", exc_info=True)
        raise

# Streamlit UI
def main():
    st.set_page_config(
        page_title="Podcast Generator",
        page_icon="🎙️",
        layout="wide"
    )
    
    st.title("🎙️ Convert Anything to Podcast with Gemini & ElevenLabs")
    
    # Check for API keys
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    
    if not ELEVENLABS_API_KEY:
        st.error("⚠️ ElevenLabs API key not found. Please set the ELEVENLABS_API_KEY environment variable.")
        st.stop()
    
    if not GEMINI_API_KEY:
        st.error("⚠️ Gemini API key not found. Please set the GEMINI_API_KEY environment variable.")
        st.stop()
    
    # Sidebar for configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        summarization_length = st.selectbox(
            "Content Summarization Level",
            ["short", "medium", "long"],
            index=1,
            help="How much to summarize long content"
        )
        
        max_tokens = st.slider(
            "Max Content Tokens",
            min_value=1000,
            max_value=10000,
            value=6000,
            step=500,
            help="Maximum tokens to process from content"
        )
        
        show_debug = st.checkbox("Show Debug Information", value=False)
    
    # Create tabs
    tab1, tab2, tab3 = st.tabs(["📄 Input", "⚙️ Settings", "🎵 Output"])
    
    with tab1:
        st.header("Select Input Type")
        
        content_source = ""
        source_type = "pdf"
        
        input_type = st.radio(
            "Choose input type:",
            ("Upload PDF", "Company Name", "Company Website URL", "Raw Text")
        )
        
        if input_type == "Upload PDF":
            uploaded_file = st.file_uploader("Upload a PDF file", type=["pdf"])
            if uploaded_file is not None:
                pdf_path = "uploaded_pdf.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                st.success("PDF uploaded successfully!")
                content_source = pdf_path
                source_type = "pdf"
            else:
                content_source = ""
                
        elif input_type == "Company Name":
            company_name = st.text_input("Enter company name:")
            content_source = company_name
            source_type = "company"
            
        elif input_type == "Company Website URL":
            website_url = st.text_input("Enter company website URL (e.g., https://www.example.com)")
            content_source = website_url
            source_type = "url"
            
        elif input_type == "Raw Text":
            raw_text = st.text_area("Paste or write your text here", height=200)
            content_source = raw_text
            source_type = "text"
    
    with tab2:
        st.header("Podcast Generation Settings")
        
        system_message = st.text_area(
            "System Instructions",
            value="Your task is to take the input text provided and transform it into an engaging, informative podcast dialogue. The input text might be messy or unstructured, possibly sourced from PDFs, web pages, or other formats. Your goal is to extract the key topics, main points, and any interesting facts that could be discussed in a podcast. Ignore any irrelevant details or formatting issues in the input.\n\n## Steps to Follow:\n\n1. Read and Analyze:\n- Carefully review the input text to identify the main topics, key points, and notable anecdotes.\n- Consider how these topics can be discussed in a way that's engaging, fun, and accessible to a general audience.\n\n2. Brainstorm Ideas:\n- Think about creative ways to present the information: use analogies, storytelling, hypothetical scenarios, or thought-provoking questions to keep the dialogue interesting.\n- Keep the tone conversational, avoiding technical jargon or assuming prior knowledge.\n- If complex concepts are necessary, explain them in simple, easy-to-understand terms.\n\n3. Create Dialogue:\n- Write a podcast dialogue using a natural, flowing conversational tone.\n- Incorporate filler words like \"hmmmmm,\" \"ahhhhh,\" \"ohhh,\" or \"you know\" naturally in the dialogue to simulate real, casual conversations.\n- Format the dialogue as:\n    ```\n    male-1: ...  \n    female-1: ...  \n    male-1: ...  \n    ```\n\n- Do not include any introductory music, sound effects, or bracketed placeholders. Start directly with dialogue.\n\n4. Key Points and Takeaways:\n- Ensure the dialogue revisits the key insights and takeaways organically toward the end of the conversation, without sounding like an obvious recap.\n\n5. Detailed and On-Topic:\n- Make the dialogue as detailed and lengthy as possible while staying engaging and on-topic. Use your full capacity to create an in-depth discussion.\n\n## Note:\n- Maintain the specified dialogue format throughout.\n- Avoid using anything other than the required dialogue format in your response.\n- Use filler words and pauses naturally to enhance the conversational flow and make it sound more authentic.",
            height=300
        )
        
        prompt = st.text_area(
            "Generation Prompt",
            value="Transform the provided content into a natural, engaging podcast dialogue with clear speaker roles, keeping the tone conversational, informative, and accessible for a general audience.",
            height=100
        )
    
    with tab3:
        st.header("Generated Podcast")
        
        # Generate button
        generate_button = st.button("🎙️ Generate Podcast", type="primary", use_container_width=True)
        
        if generate_button:
            # Validate input
            if not content_source:
                st.error(f"⚠️ Please provide {input_type.lower()} first.")
            elif source_type == "url" and not content_source.startswith("http"):
                st.error("⚠️ Please enter a valid URL starting with http:// or https://")
            else:
                # Set up progress tracking
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    # Step 1: Initial preparation
                    status_text.text("📊 Analyzing input...")
                    progress_bar.progress(10)
                    
                    # Step 2: Process input based on type
                    if source_type == "url":
                        status_text.text("🌐 Extracting website content...")
                        progress_bar.progress(20)
                    elif source_type == "company":
                        status_text.text(f"🔍 Researching {content_source}...")
                        progress_bar.progress(20)
                    elif source_type == "pdf":
                        status_text.text("📄 Processing PDF content...")
                        progress_bar.progress(20)
                    elif source_type == "text":
                        status_text.text("📝 Processing text input...")
                        progress_bar.progress(20)
                    
                    # Step 3: Generate podcast
                    status_text.text("🎙️ Creating podcast dialogue...")
                    progress_bar.progress(40)
                    
                    # Call the generate_podcast function
                    podcast_result = generate_podcast(
                        prompt=prompt, 
                        system_message=system_message, 
                        content_source=content_source,
                        source_type=source_type
                    )
                    
                    # Step 4: Generate audio
                    status_text.text("🔊 Audio generation completed!")
                    progress_bar.progress(90)
                    
                    # Step 5: Final processing
                    status_text.text("✅ Finalizing podcast...")
                    progress_bar.progress(100)
                    
                    # Get the results
                    audio_path = podcast_result.get("audio_path")
                    transcript_path = podcast_result.get("transcript_path")
                    
                    # Display success message
                    status_text.text("✅ Podcast generated successfully!")
                    
                    # Display the results
                    st.success("🎉 Your podcast is ready!")
                    
                    # Show statistics
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total Items", podcast_result.get("total_items", 0))
                    with col2:
                        st.metric("Processed Items", podcast_result.get("processed_items", 0))
                    with col3:
                        st.metric("File Size", podcast_result.get("file_size", "Unknown"))
                    
                    if audio_path and os.path.exists(audio_path):
                        st.subheader("🔊 Audio")
                        st.audio(audio_path, format="audio/mp3")
                        
                        with open(audio_path, "rb") as audio_file:
                            st.download_button(
                                label="⬇️ Download Audio",
                                data=audio_file.read(),
                                file_name="podcast.mp3",
                                mime="audio/mp3"
                            )
                    else:
                        st.warning("⚠️ Audio file was not generated successfully.")
                    
                    if transcript_path and os.path.exists(transcript_path):
                        st.subheader("📝 Transcript")
                        with open(transcript_path, "r", encoding="utf-8") as f:
                            transcript = f.read()
                        st.text_area("Full Transcript", transcript, height=300, key="transcript_display")
                        st.download_button(
                            label="⬇️ Download Transcript",
                            data=transcript,
                            file_name="podcast_transcript.txt",
                            mime="text/plain"
                        )
                    else:
                        st.warning("⚠️ Transcript file was not generated successfully.")
                    
                    # Show debug info if enabled
                    if show_debug:
                        st.subheader("🔍 Debug Information")
                        debug_info = {
                            "Content Source Type": source_type,
                            "Content Source": content_source[:100] + "..." if len(str(content_source)) > 100 else content_source,
                            "Summarization Level": summarization_length,
                            "Max Tokens": max_tokens,
                            "Processing Timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "Podcast Result Keys": list(podcast_result.keys()),
                            "Audio Path Exists": os.path.exists(audio_path) if audio_path else False,
                            "Transcript Path Exists": os.path.exists(transcript_path) if transcript_path else False
                        }
                        st.json(debug_info)
                        
                except FileNotFoundError as e:
                    st.error(f"❌ File Error: {e}")
                    progress_bar.progress(0)
                    status_text.text("❌ Error occurred")
                except ValueError as e:
                    progress_bar.progress(0)
                    status_text.text("❌ Error occurred")
                    if "rate limit" in str(e).lower():
                        st.error("⚠️ ElevenLabs API rate limit exceeded. Please try again later or reduce the text length.")
                    elif "invalid" in str(e).lower() and "api key" in str(e).lower():
                        st.error("⚠️ Your ElevenLabs API key appears to be invalid or expired.")
                        st.info("💡 Check your API key at https://elevenlabs.io/app/account")
                    elif "voice id" in str(e).lower():
                        st.error("⚠️ Voice ID not found. The configured voice IDs may no longer be valid.")
                        st.info("💡 Update the voice_id method in the DialogueItem class with valid voice IDs from your ElevenLabs account.")
                    elif "quota" in str(e).lower():
                        st.error("⚠️ API quota exceeded. Please check your API usage limits.")
                    else:
                        st.error(f"❌ API Error: {e}")
                except Exception as e:
                    progress_bar.progress(0)
                    status_text.text("❌ Error occurred")
                    st.error(f"❌ An unexpected error occurred: {e}")
                    logger.exception("Unhandled exception in main")
                    
                    if show_debug:
                        st.subheader("🔍 Error Details")
                        st.exception(e)

if __name__ == "__main__":
    main()