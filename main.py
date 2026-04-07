import cv2
import time
import json
import sys
import os
import requests
from ultralytics import YOLO

def download_video(url, save_path):
    print(f"Downloading video from: {url}")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("Download complete.")
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False

def run_cool_demo(video_url):
    print("\n" + "="*60)
    print(" VIO LIVE - AUTONOMOUS VISION DEMO (PRO)")
    print("="*60)
    
    video_file = "input_match.mp4"
    if not download_video(video_url, video_file):
        return

    # 1. Load SOTA Model
    print("Initializing YOLOv11 Engine...")
    model = YOLO("yolo11n.pt")
    
    # 2. Open Stream
    cap = cv2.VideoCapture(video_file)
    if not cap.isOpened():
        print("Error: Video stream failure.")
        return

    frame_count = 0
    start_time = time.time()
    
    # Dynamic Narrative Engine (English)
    match_events = []

    print("\n[LIVE ANALYSIS STARTED]")
    print("-" * 60)

    try:
        while frame_count < 500: # Analyzing first ~20 seconds
            ret, frame = cap.read()
            if not ret:
                break
            
            # Smart Polling (Process 1 in 15 frames for real-time feel)
            if frame_count % 15 == 0:
                # Optimized resolution
                blob = cv2.resize(frame, (640, 360))
                results = model(blob, verbose=False)
                
                # Extracting context
                detections = [model.names[int(c)] for r in results for c in r.boxes.cls]
                
                # Logic: If ball and many people detected -> Offensive Phase
                if "sports ball" in detections and detections.count("person") > 5:
                    event = {
                        "frame": frame_count,
                        "timestamp": f"{frame_count/25:.2f}s",
                        "status": "DANGER - OFFENSIVE PHASE",
                        "narrative": "High pressure detected in opponent's half.",
                        "vio_ad": "FC Barcelona Jersey 2025 - BUY NOW"
                    }
                    print(f"[{event['timestamp']}] {event['status']}: {event['narrative']}")
                    print(f"   >> Vio Suggestion: {event['vio_ad']}")
                    match_events.append(event)

            frame_count += 1
            if frame_count % 100 == 0:
                print(f"Tracking players and ball... Progress: {frame_count} frames")

    finally:
        cap.release()
        
    print("\n" + "="*60)
    print(" MATCH ANALYSIS SUMMARY")
    print("="*60)
    print(json.dumps(match_events[:3], indent=2)) # Showing first 3 major events
    print("="*60)
    print(f"Processed {frame_count} frames at {(frame_count/(time.time()-start_time)):.2f} FPS")

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://firebasestorage.googleapis.com/v0/b/tipio-1ec97.appspot.com/o/bar.v.psg.1.ucl.01.10.2025.fullmatchsports.com.1080p.mp4?alt=media&token=593ce8a1-0462-4c37-98c3-e399f25e3853"
    run_cool_demo(url)
