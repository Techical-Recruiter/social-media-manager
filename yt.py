import streamlit as st
import httpx
import asyncio
import os
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import pytz
import re
import pickle

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import googleapiclient.discovery
import googleapiclient.http

from agents import Agent, Runner, function_tool, AsyncOpenAI, OpenAIChatCompletionsModel, set_tracing_disabled

load_dotenv()

gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    st.error("GEMINI_API_KEY environment variable is not set. Please set it in your .env file.")
    st.stop()

FACEBOOK_PAGE_ACCESS_TOKEN = "EAAPAqe5BMrABO1D0LHLZA0bFt47RODbjQ3ua3TGHWleZCTFvZBgZA58ZCIzQegPtLzxILM5kyPueQE1FFBQKQikv1nHimfivs1z7Vv66PJusJ0Jxms4ovjgc5dYRq9Ko1ebQaD89oJKpZAHO6LzxAJuv4Lk7RdX12ZBDZCtR9GZB3yAAasMSwp77Q1QQYDq7TFhgJwYZCftv5bRtSTgNO2DeZA6SeZAxxkrii1ZC6VNZCqMo1i5S4ZD"
FACEBOOK_PAGE_ID = "61560363938950"

LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PROFILE_ID = os.getenv("LINKEDIN_PROFILE_ID")

if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PROFILE_ID:
    st.warning("LinkedIn ACCESS_TOKEN or PROFILE_ID not found in .env. LinkedIn posting might not work.")

KARACHI_TZ = pytz.timezone('Asia/Karachi')

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_CONFIG = {
    "installed": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]
    }
}
TOKEN_PICKLE_FILE = "token.pickle"

provider = AsyncOpenAI(
    api_key=gemini_api_key,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)
model = OpenAIChatCompletionsModel(
    openai_client=provider,
    model="gemini-1.5-flash",
)
set_tracing_disabled(disabled=True)


async def post_to_linkedin(message: str, linkedin_profile_id: str) -> Dict[str, Any]:
    if not LINKEDIN_ACCESS_TOKEN:
        return {"error": "LinkedIn Access Token not configured."}

    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json"
    }
    post_data = {
        "author": f"urn:li:person:{linkedin_profile_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": message
                },
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }

    async with httpx.AsyncClient() as client:
        try:
            post_response = await client.post(
                "https://api.linkedin.com/v2/ugcPosts",
                headers=headers,
                json=post_data,
                timeout=30.0
            )
            post_response.raise_for_status()
            return {"success": "LinkedIn post published!", "response": post_response.json()}
        except httpx.RequestError as e:
            return {"error": f"LinkedIn Network error: {e}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"LinkedIn HTTP error: {e.response.status_code} - {e.response.text}"}

async def post_to_facebook_page(content: str) -> Dict[str, Any]:
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        return {"error": "Facebook Page ID or Access Token not configured."}

    graph_api_url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/feed"
    headers = {
        "Content-Type": "application/json"
    }
    params = {
        "message": content,
        "access_token": FACEBOOK_PAGE_ACCESS_TOKEN
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(graph_api_url, params=params, headers=headers, timeout=30.0)
            response.raise_for_status()
            return {"success": "Facebook post published!", "response": response.json()}
        except httpx.RequestError as e:
            return {"error": f"Facebook Network error during post: {e}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"Facebook HTTP error: {e.response.status_code} - {e.response.text}"}

def youtube_authenticate():
    credentials = None
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            credentials = pickle.load(token)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, YOUTUBE_SCOPES)
            credentials = flow.run_local_server(port=0)
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(credentials, token)
    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)

async def upload_youtube_video(file_path: str, title: str, description: str, tags: List[str], privacy_status: str = "private") -> Dict[str, Any]:
    youtube = youtube_authenticate()
    try:
        if not title.strip():
            raise ValueError("Video title cannot be empty.")
        if not description.strip():
            description = "No description provided."

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": privacy_status
            }
        }

        insert_request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=googleapiclient.http.MediaFileUpload(file_path, chunksize=-1, resumable=True)
        )

        response = None
        st.write("Uploading video... This may take a while.")
        progress_bar = st.progress(0, text="Upload Progress")
        while response is None:
            status, response = insert_request.next_chunk()
            if status:
                progress_bar.progress(status.resumable_progress, text=f"Uploaded {int(status.resumable_progress * 100)}%")

        progress_bar.empty()
        video_url = f"https://www.youtube.com/watch?v={response['id']}"
        return {
            "success": "Video uploaded successfully!",
            "video_id": response["id"],
            "video_url": video_url,
            "response": response
        }

    except Exception as e:
        return {"error": f"Error uploading video: {e}"}

social_media_agent = Agent(
    name="Social Media Content Generator",
    tools=[],
    model=model,
    instructions=(
        "You are a highly skilled social media content writer. "
        "Your primary role is to create concise, engaging, and professional text content for social media posts, "
        "specifically for LinkedIn and Facebook. "
        "- Generate only the post content. DO NOT include any introductory phrases.\n"
        "- DO NOT use any emojis or smilies in your response.\n"
        "- DO NOT use any markdown formatting in your response.\n"
        "- Focus on tone, structure, clarity, and audience engagement.\n"
        "- Ensure the content sounds like it was written by a human."
    )
)

youtube_agent = Agent(
    name="YouTube Content Creator",
    tools=[],
    model=model,
    instructions=(
        "You are a professional YouTube content writer. "
        "Create engaging, professional, and SEO-friendly text content for YouTube video titles and descriptions. "
        "- Generate only the requested content (title and description).\n"
        "- DO NOT use any emojis or smilies.\n"
        "- DO NOT use any markdown formatting.\n"
        "- Provide a 'Title:' and 'Description:' section clearly.\n"
        "- The title should be concise and clickable (70-100 chars max).\n"
        "- The description should be detailed and include relevant keywords.\n"
        "- Maintain a professional, authoritative tone."
    )
)

def initialize_session_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "generated_post_content" not in st.session_state:
        st.session_state.generated_post_content = None
    if "awaiting_schedule_datetime_input" not in st.session_state:
        st.session_state.awaiting_schedule_datetime_input = False
    if "temp_time_input" not in st.session_state:
        st.session_state.temp_time_input = ""
    if "linkedin_id_input" not in st.session_state:
        st.session_state.linkedin_id_input = LINKEDIN_PROFILE_ID if LINKEDIN_PROFILE_ID else ""
    if "generated_youtube_content" not in st.session_state:
        st.session_state.generated_youtube_content = None
    if "temp_video_path" not in st.session_state:
        st.session_state.temp_video_path = None
    if "youtube_authenticated" not in st.session_state:
        st.session_state.youtube_authenticated = False
    if "video_topic" not in st.session_state:
        st.session_state.video_topic = ""
    if "editable_video_title" not in st.session_state:
        st.session_state.editable_video_title = ""
    if "editable_video_description" not in st.session_state:
        st.session_state.editable_video_description = ""
    if "current_tab" not in st.session_state:
        st.session_state.current_tab = "Social Media"

def show_social_media_tab():
    st.header("📝 Social Media Posts")
    st.write("Create and publish posts for LinkedIn and Facebook")

    if prompt := st.chat_input("What kind of post would you like to create today?", key="social_media_input"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Generating post content..."):
                run_result = asyncio.run(Runner.run(starting_agent=social_media_agent, input=prompt))
                generated_content = str(run_result.final_output).strip()

                if generated_content:
                    st.session_state.generated_post_content = generated_content
                    st.markdown("Here's a draft for your post:")
                    st.info(generated_content)
                    st.session_state.messages.append({"role": "assistant", "content": generated_content})
                    st.session_state.messages.append({"role": "assistant", "content": "Ready to publish? Choose an option below."})
                else:
                    st.error("I couldn't generate the post content at this time. Please try again.")
                    st.session_state.messages.append({"role": "assistant", "content": "I couldn't generate the post content at this time. Please try again."})
                st.rerun()

    if st.session_state.generated_post_content and st.session_state.messages and st.session_state.messages[-1]["content"] == "Ready to publish? Choose an option below.":
        st.divider()
        st.subheader("Post Actions")

        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("Post to LinkedIn Now", key="linkedin_post_button"):
                if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PROFILE_ID:
                    st.error("LinkedIn credentials not configured.")
                else:
                    with st.spinner("Publishing to LinkedIn..."):
                        result = asyncio.run(post_to_linkedin(
                            message=st.session_state.generated_post_content,
                            linkedin_profile_id=LINKEDIN_PROFILE_ID
                        ))
                        if "success" in result:
                            st.success(result["success"])
                            st.balloons()
                            st.session_state.generated_post_content = None
                        else:
                            st.error(result.get("error", "Unknown error"))
                        st.rerun()

        with col2:
            if st.button("Post to Facebook Now", key="facebook_post_button"):
                if not FACEBOOK_PAGE_ACCESS_TOKEN:
                    st.error("Facebook credentials not configured.")
                else:
                    with st.spinner("Publishing to Facebook..."):
                        result = asyncio.run(post_to_facebook_page(
                            content=st.session_state.generated_post_content
                        ))
                        if "success" in result:
                            st.success(result["success"])
                            st.session_state.generated_post_content = None
                        else:
                            st.error(result.get("error", "Unknown error"))
                        st.rerun()

        if st.button("Clear Post", key="clear_post_button"):
            st.session_state.generated_post_content = None
            st.rerun()

def show_youtube_tab():
    st.header("🎬 YouTube Video Upload")
    st.write("Upload videos with professional titles and descriptions")

    if not st.session_state.youtube_authenticated:
        if st.button("Authenticate YouTube", key="youtube_auth_button"):
            try:
                youtube_authenticate()
                st.session_state.youtube_authenticated = True
                st.success("YouTube authentication successful!")
                st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")
                st.rerun()
    
    st.subheader("Step 1: Upload Your Video")
    uploaded_file = st.file_uploader("Choose a video file", type=["mp4", "mov", "avi"])
    
    if uploaded_file is not None:
        temp_dir = "temp_uploads"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, uploaded_file.name)
        with open(temp_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.session_state.temp_video_path = temp_path
        st.success(f"Video '{uploaded_file.name}' uploaded successfully!")
        st.video(temp_path)

    if st.session_state.temp_video_path:
        st.subheader("Step 2: Generate Video Metadata")
        topic = st.text_input("Describe your video content:", key="video_topic_input")
        
        if st.button("Generate Title & Description", key="generate_metadata_button") and topic:
            with st.spinner("Creating professional metadata..."):
                result = asyncio.run(Runner.run(
                    starting_agent=youtube_agent,
                    input=f"Create YouTube video title and description for: {topic}"
                ))
                content = str(result.final_output).strip()
                
                try:
                    title = content.split("Title:")[1].split("Description:")[0].strip()
                    description = content.split("Description:")[1].strip()
                    
                    st.session_state.editable_video_title = title
                    st.session_state.editable_video_description = description
                    st.session_state.generated_youtube_content = content
                    st.success("Metadata generated successfully!")
                except:
                    st.error("Couldn't parse the generated content. Please try again.")
                    st.text_area("Raw Output", value=content, height=200)
                st.rerun()

    if st.session_state.generated_youtube_content:
        st.subheader("Step 3: Review and Upload")
        
        st.text_input("Video Title", 
                      value=st.session_state.editable_video_title, 
                      key="final_title_input")
        
        st.text_area("Video Description", 
                     value=st.session_state.editable_video_description, 
                     height=300,
                     key="final_description_input")
        
        privacy_status = st.radio("Privacy Status", 
                                 ["public", "private", "unlisted"],
                                 index=1,
                                 key="privacy_radio")
        
        if st.button("Upload to YouTube", key="upload_button"):
            if not st.session_state.youtube_authenticated:
                st.error("Please authenticate with YouTube first")
            else:
                with st.spinner("Uploading video to YouTube..."):
                    result = asyncio.run(upload_youtube_video(
                        file_path=st.session_state.temp_video_path,
                        title=st.session_state.editable_video_title,
                        description=st.session_state.editable_video_description,
                        tags=[],
                        privacy_status=privacy_status
                    ))
                    
                    if "success" in result:
                        st.success(f"Upload successful! Video URL: {result['video_url']}")
                        st.balloons()
                        if os.path.exists(st.session_state.temp_video_path):
                            os.remove(st.session_state.temp_video_path)
                        st.session_state.temp_video_path = None
                        st.session_state.generated_youtube_content = None
                    else:
                        st.error(f"Upload failed: {result.get('error', 'Unknown error')}")
                    st.rerun()

def main():
    st.set_page_config(
        page_title="Multi-Platform Post Bot",
        page_icon="📝",
        layout="wide"
    )
    
    initialize_session_state()
    
    st.title("📝 Multi-Platform Post Bot")
    st.write("Create and publish content across multiple platforms")
    
    tab1, tab2 = st.tabs(["Social Media Posts", "YouTube Video Upload"])
    
    with tab1:
        show_social_media_tab()
    
    with tab2:
        show_youtube_tab()
    
    st.sidebar.header("Configuration")
    st.sidebar.write("Ensure your .env file contains all required API keys")

if __name__ == "__main__":
    main()