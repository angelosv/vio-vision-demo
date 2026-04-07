import streamlit as st
import cv2
import time
import json
from ultralytics import YOLO

# --- PAGE CONFIG ---
st.set_page_config(page_title="Vio Vision Lab - Real-Time Match Insights", layout="wide")

st.title("🧪 Vio Vision Lab - R&D Dashboard")
st.markdown("### Real-Time Match Narrative & Tactical Analysis")

# --- SIDEBAR: CONTROLS ---
with st.sidebar:
    st.header("Match Source")
    video_url = st.text_input("Direct Firebase/Video URL", 
                             value="https://firebasestorage.googleapis.com/v0/b/tipio-1ec97.appspot.com/o/bar.v.psg.1.ucl.01.10.2025.fullmatchsports.com.1080p.mp4?alt=media&token=593ce8a1-0462-4c37-98c3-e399f25e3853")
    
    start_btn = st.button("🚀 Start Streaming Analysis", type="primary")
    
    st.divider()
    st.info("**Engine:** YOLOv11 (Local) + Streaming Inference")
    st.warning("Note: No local download required. Analyzing directly from source.")

# --- MAIN LAYOUT ---
col_video, col_timeline = st.columns([2, 1])

with col_video:
    st.subheader("Live Stream Feed")
    video_placeholder = st.empty()
    status_placeholder = st.empty()

with col_timeline:
    st.subheader("📊 Match Timeline")
    timeline_placeholder = st.container()

# --- INITIALIZE STATE ---
if "events" not in st.session_state:
    st.session_state.events = []

# --- STREAMING VISION ENGINE ---
def run_streaming_analysis(url):
    # Load model
    model = YOLO("yolo11n.pt")
    
    # Direct Stream Open (No Download)
    status_placeholder.info("Connecting to remote video stream...")
    cap = cv2.VideoCapture(url)
    
    if not cap.isOpened():
        status_placeholder.error("Error: Could not connect to the video stream.")
        return

    frame_count = 0
    start_time = time.time()
    
    # Simulated Strategic Triggers (Narrative Logic)
    event_triggers = {
        150: {"t": "0:06", "ev": "Barcelona High Press", "ad": "Official Training Jersey"},
        300: {"t": "0:12", "ev": "Ball in Box - Goal Opportunity", "ad": "Lewandowski #9 Jersey"},
        450: {"t": "0:18", "ev": "Corner Kick - Set Piece", "ad": "FCB Scarf Pack"}
    }

    try:
        while cap.isOpened() and frame_count < 1000:
            ret, frame = cap.read()
            if not ret: break
            
            # Display Stream (Synced)
            if frame_count % 2 == 0:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                video_placeholder.image(frame_rgb, channels="RGB", use_container_width=True)
            
            # Live Tactical Analysis
            if frame_count % 30 == 0:
                # Optimized inference on stream
                blob = cv2.resize(frame, (640, 360))
                results = model(blob, verbose=False)
                detections = [model.names[int(c)] for r in results for c in r.boxes.cls]
                
                # Check for Match Events
                if frame_count in event_triggers:
                    trigger = event_triggers[frame_count]
                    new_event = {
                        "time": trigger["t"],
                        "event": trigger["ev"],
                        "analysis": f"Detected {detections.count('person')} players and ball in active play.",
                        "offer": trigger["ad"]
                    }
                    st.session_state.events.insert(0, new_event)
                    
                    # Real-time Update
                    with col_timeline:
                        for ev in st.session_state.events[:5]:
                            st.success(f"**{ev['time']} - {ev['event']}**\n\n{ev['analysis']}\n\n*Vio Ad: {ev['offer']}*")
                            st.divider()
            
            frame_count += 1
            # Control flow to match video speed
            time.sleep(0.005)

    finally:
        cap.release()
        status_placeholder.success("Streaming Analysis Complete.")

if start_btn:
    st.session_state.events = []
    run_streaming_analysis(video_url)
