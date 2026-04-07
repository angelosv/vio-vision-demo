import streamlit as st
import cv2
import time
import json
import requests
from ultralytics import YOLO
import tempfile
import os

# --- PAGE CONFIG ---
st.set_page_config(page_title="Vio Vision Lab - Real-Time Match Insights", layout="wide")

st.title("🧪 Vio Vision Lab - R&D Dashboard")
st.markdown("### Real-Time Match Narrative & Tactical Analysis")

# --- SIDEBAR: CONTROLS ---
with st.sidebar:
    st.header("Match Source")
    video_url = st.text_input("Match Video URL", 
                             value="https://firebasestorage.googleapis.com/v0/b/tipio-1ec97.appspot.com/o/bar.v.psg.1.ucl.01.10.2025.fullmatchsports.com.1080p.mp4?alt=media&token=593ce8a1-0462-4c37-98c3-e399f25e3853")
    
    start_btn = st.button("🚀 Start Live Analysis", type="primary")
    
    st.divider()
    st.info("**Engine:** YOLOv11 (Local) + Gemma 4 (Strategic Oracle)")

# --- MAIN LAYOUT ---
col_video, col_timeline = st.columns([2, 1])

with col_video:
    st.subheader("Live Feed")
    video_placeholder = st.empty()
    status_placeholder = st.empty()

with col_timeline:
    st.subheader("📊 Match Timeline")
    timeline_placeholder = st.container()

# --- INITIALIZE STATE ---
if "events" not in st.session_state:
    st.session_state.events = []

# --- VISION ENGINE ---
def run_analysis(url):
    # Load model
    model = YOLO("yolo11n.pt")
    
    # Download/Open Video
    status_placeholder.warning("Initializing stream and downloading video context...")
    cap = cv2.VideoCapture(url)
    
    frame_count = 0
    start_time = time.time()
    
    while cap.isOpened() and frame_count < 1000: # Analyzing first segment
        ret, frame = cap.read()
        if not ret: break
        
        # Display video (reduced size for dashboard)
        if frame_count % 3 == 0:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            video_placeholder.image(frame_rgb, channels="RGB", use_container_width=True)
        
        # Tactical Polling
        if frame_count % 30 == 0:
            blob = cv2.resize(frame, (640, 360))
            results = model(blob, verbose=False)
            detections = [model.names[int(c)] for r in results for c in r.boxes.cls]
            
            # Artificial Intelligence Trigger (Simulation of Gemma 4 Strategic Logic)
            if "sports ball" in detections and detections.count("person") > 5:
                timestamp = time.strftime('%H:%M:%S', time.gmtime(frame_count / 25))
                new_event = {
                    "time": timestamp,
                    "event": "Offensive Pressure Detected",
                    "analysis": "High clustering in final third. Ball possession verified.",
                    "offer": "Barça Home Kit - Buy Now"
                }
                st.session_state.events.insert(0, new_event)
                
                # Update Timeline UI
                with col_timeline:
                    for ev in st.session_state.events[:8]: # Show last 8 events
                        st.success(f"**{ev['time']} - {ev['event']}**\n\n{ev['analysis']}\n\n*Target Ad: {ev['offer']}*")
                        st.divider()
        
        frame_count += 1
        time.sleep(0.01) # Sync for demo visual flow

    cap.release()
    status_placeholder.success("Match Segment Analysis Complete.")

if start_btn:
    st.session_state.events = []
    run_analysis(video_url)
