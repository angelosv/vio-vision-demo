# Vio Vision gRPC Client Example

Sample Python client demonstrating how to consume the `MatchEventsService`.
This is what Viaplay / TV2 would integrate into their broadcast graphics or
data pipelines.

## Prerequisites

```bash
pip install grpcio grpcio-tools
```

## Generate stubs (if not committed)

```bash
python -m grpc_tools.protoc \
  -I=../../shared/proto \
  --python_out=. \
  --grpc_python_out=. \
  ../../shared/proto/match_events.proto
```

## Run the demo

```bash
# 1. List past sessions
python demo.py list

# 2. Stream live events (replace with real session_id from /api/sessions/active)
python demo.py stream abc12345

# 3. Fetch historical events for a past session
python demo.py history abc12345 --start 0 --end 300
```

## Auth

Pass API key via metadata header:

```bash
python demo.py stream abc12345 --api-key YOUR_KEY_HERE
```
