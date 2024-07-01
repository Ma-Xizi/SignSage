import os
from typing import List
from extract_frames import extract_frames
from gtts import gTTS
import speech_recognition as sr
from moviepy.editor import VideoFileClip, concatenate_videoclips
from langchain.chains import TransformChain
from langchain_core.messages import HumanMessage
from langchain_core.runnables import chain
from langchain_openai import ChatOpenAI
import base64
import logging

API_KEY = "APIMY"

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Set up ChatOpenAI LLM
llm = ChatOpenAI(
    temperature=0.5,
    max_tokens=1024,
    timeout=None,
    max_retries=2,
    api_key=API_KEY
)

# Function to load image and encode it as base64
def load_image(inputs: dict) -> dict:
    image_path = inputs["image_path"]

    def encode_image(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    image_base64 = encode_image(image_path)
    return {"image": image_base64}

# Define the TransformChain
load_image_chain = TransformChain(
    input_variables=["image_path"],
    output_variables=["image"],
    transform=load_image
)

# Function to describe the image using the model
@chain
def image_model(inputs: dict) -> str:
    model = ChatOpenAI(temperature=0.5, max_tokens=1024, model="gpt-4o", api_key=API_KEY)
    msg = model.invoke(
        [HumanMessage(
            content=[
                {"type": "text", "text": inputs["prompt"]},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{inputs['image']}"}},
            ])]
    )
    return msg.content if msg else ""

def extract_audio_from_video(video_path: str, audio_path: str, start_time: float, end_time: float):
    video = VideoFileClip(video_path)
    audio = video.subclip(start_time, end_time).audio
    audio.write_audiofile(audio_path, codec='pcm_s16le')

def convert_audio_to_text(audio_path: str) -> str:
    recognizer = sr.Recognizer()
    audio_file = sr.AudioFile(audio_path)
    with audio_file as source:
        audio = recognizer.record(source)
    try:
        text = recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        text = ""
    return text

def get_image_information(image_path: str) -> str:
    vision_prompt = "Summarize the image you see"
    vision_chain = load_image_chain | image_model 
    response = vision_chain.invoke({'image_path': image_path, 'prompt': vision_prompt})
    return response

def generate_summary(descriptions: List[str]) -> str:
    combined_descriptions = " ".join(descriptions)
    summary_prompt = f"The following descriptions are for video frames, summarize into a coherent summary of the video:\n\n{combined_descriptions}"
    summary_message = llm.invoke(summary_prompt)
    return summary_message.content if summary_message else ""

def process_video_part(video_part_path: str, frames_directory: str, start_time: float, end_time: float, part_number: int) -> str:
    part_frames_directory = os.path.join(frames_directory, f"part_{part_number}")
    os.makedirs(part_frames_directory, exist_ok=True)
    
    logging.info(f"Extracting frames for part {part_number} from {start_time} to {end_time}")
    extract_frames(video_part_path, part_frames_directory, frame_interval=2)
    frame_files = [os.path.join(part_frames_directory, f) for f in os.listdir(part_frames_directory) if f.endswith('.jpg')]
    
    if not frame_files:
        logging.error(f"No frames were extracted for part {part_number}.")
        return ""
    
    descriptions = [get_image_information(frame) for frame in frame_files]
    
    # Extract and convert audio to text for the current segment
    audio_path = os.path.join(part_frames_directory, "audio.wav")
    extract_audio_from_video(video_part_path, audio_path, start_time, end_time)
    audio_text = convert_audio_to_text(audio_path)
    
    descriptions.append(audio_text)
    return generate_summary(descriptions)

def process_video(video_path: str, num_parts: int = 3) -> str:
    video = VideoFileClip(video_path)
    video_duration = int(video.duration)
    
    # Calculate the duration for each part
    part_duration = video_duration / num_parts
    
    video_parts = []
    for i in range(num_parts):
        start_time = i * part_duration
        end_time = min((i + 1) * part_duration, video_duration)  # Ensure end_time does not exceed video duration
        part_path = f"part_{start_time:.2f}_{end_time:.2f}.mp4"  # Use float formatting for part path
        try:
            video_part = video.subclip(start_time, end_time)
            video_part.write_videofile(part_path)
            video_parts.append((part_path, start_time, end_time))  # Store start and end times
        except Exception as e:
            logging.error(f"Error processing video part from {start_time} to {end_time}: {e}")
            continue
    
    frames_directory = "frames"
    summaries = [process_video_part(part[0], frames_directory, part[1], part[2], i) for i, part in enumerate(video_parts)]
    combined_summary = "\n\n".join([summary for summary in summaries if summary])  # Combine non-empty summaries
    
    # Combine video parts back into a single video
    processed_clips = []
    for part, start_time, end_time in video_parts:
        try:
            processed_clip = VideoFileClip(part)
            processed_clips.append(processed_clip)
        except Exception as e:
            logging.error(f"Error loading video part {part}: {e}")
            continue
    
    final_video = concatenate_videoclips(processed_clips)
    final_video_path = "final_video_with_summary.mp4"
    final_video.write_videofile(final_video_path, codec='libx264', audio_codec='aac')
    
    return combined_summary

def get_video_summary(video_path: str, frames_directory: str) -> str:
    return process_video(video_path, num_parts=3)
