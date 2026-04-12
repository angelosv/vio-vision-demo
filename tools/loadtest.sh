#!/usr/bin/env bash
# Load test the Vio Vision stack with multiple concurrent streams.
#
# Strategy:
#   1. Spawn N ffmpeg processes that each stream a local MP4 to a SRT port
#      the ingestion service listens on (simulating broadcast contribution)
#   2. Kick off N analysis sessions via the gateway REST API
#   3. Monitor Prometheus metrics during the test
#
# Usage:
#   bash tools/loadtest.sh [N_STREAMS] [VIDEO_FILE]
#   bash tools/loadtest.sh 3 ./sample.mp4

set -eu

N=${1:-1}
VIDEO=${2:-./sample.mp4}
GATEWAY=${GATEWAY:-http://localhost:8000}
SRT_BASE_PORT=${SRT_BASE_PORT:-10000}
API_KEY=${VIO_API_KEY:-}

echo "=== Vio Vision load test ==="
echo "Streams:  $N"
echo "Video:    $VIDEO"
echo "Gateway:  $GATEWAY"
echo

if [ ! -f "$VIDEO" ]; then
  echo "Video file not found: $VIDEO"
  echo "Hint: download any sample MP4 or point to a local game recording."
  exit 1
fi

command -v ffmpeg >/dev/null || { echo "ffmpeg required"; exit 1; }
command -v curl >/dev/null || { echo "curl required"; exit 1; }

PIDS=()
cleanup() {
  echo
  echo "Stopping streams..."
  for pid in "${PIDS[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  curl -s -X POST "$GATEWAY/api/stop" -o /dev/null || true
  echo "Done"
}
trap cleanup EXIT INT TERM

AUTH_HEADER=""
if [ -n "$API_KEY" ]; then
  AUTH_HEADER="-H x-api-key:$API_KEY"
fi

for i in $(seq 1 "$N"); do
  port=$((SRT_BASE_PORT + i))
  url="srt://localhost:$port?mode=caller"

  echo "[$i] starting ffmpeg → $url"
  # Note: for a real test we'd need an SRT listener side. For the demo we use
  # HLS as the input format by pointing the gateway at a local HTTP file.
  #   ffmpeg -re -i "$VIDEO" -c:v libx264 -c:a aac -f mpegts "$url" &
  # For now, point the ingestion service directly to the file:
  curl -s -X POST "$GATEWAY/api/start" \
    -H "Content-Type: application/json" \
    $AUTH_HEADER \
    -d "{\"url\":\"$VIDEO\"}" &
  PIDS+=($!)
done

echo
echo "All streams launched. Monitoring metrics every 5s (Ctrl-C to stop)..."
echo

while true; do
  sleep 5
  frames=$(curl -s "$GATEWAY/metrics" | grep "^vio_gateway_events_persisted_total" | awk '{sum += $2} END {print sum+0}')
  ws=$(curl -s "$GATEWAY/metrics" | grep "^vio_gateway_ws_connections" | awk '{sum += $2} END {print sum+0}')
  sessions=$(curl -s "$GATEWAY/metrics" | grep "^vio_gateway_sessions_created_total" | head -1 | awk '{print $2+0}')
  printf "  [%s] sessions=%s events=%s ws_conns=%s\n" "$(date +%H:%M:%S)" "$sessions" "$frames" "$ws"
done
