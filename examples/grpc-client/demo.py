"""Reference client for the Vio Vision MatchEventsService.

Demonstrates:
  - ListSessions: paginated history
  - StreamEvents: server-streaming live frames
  - GetSessionEvents: server-streaming historical events

Usage:
  python demo.py list [--limit 10]
  python demo.py stream <session_id> [--filter shot_on_goal]
  python demo.py history <session_id> [--start 0] [--end 300]
  python demo.py stream <session_id> --api-key VIAPLAY_KEY_xxx

The same binary wire protocol is consumable from Go, Node, Java, C#, etc.
See shared/proto/match_events.proto for all types.
"""

import argparse
import os
import sys
from typing import Iterable, Optional

import grpc

# Generated stubs (run make proto at repo root or follow README)
try:
    import match_events_pb2 as pb
    import match_events_pb2_grpc as pb_grpc
except ImportError:
    print("Generated stubs not found. Run:")
    print("  python -m grpc_tools.protoc -I=../../shared/proto "
          "--python_out=. --grpc_python_out=. "
          "../../shared/proto/match_events.proto")
    sys.exit(1)


DEFAULT_TARGET = os.getenv("VIO_GRPC_TARGET", "localhost:50051")


def _metadata(api_key: Optional[str]) -> Iterable[tuple]:
    if api_key:
        return [("x-api-key", api_key)]
    return []


def cmd_list(stub, args):
    req = pb.ListSessionsRequest(limit=args.limit, offset=0)
    resp = stub.ListSessions(req, metadata=_metadata(args.api_key))
    print(f"Total: {resp.total}\n")
    for s in resp.sessions:
        status = pb.SessionStatus.Name(s.status).replace("STATUS_", "")
        duration = f"{s.duration_sec:.1f}s" if s.duration_sec > 0 else "—"
        print(f"  {s.id}  [{status:<10}] {s.source_type:<5}  "
              f"{s.home_team or '?':<20} vs {s.away_team or '?':<20}  {duration}")


def cmd_stream(stub, args):
    filters = args.filter or []
    req = pb.StreamEventsRequest(
        session_id=args.session_id,
        event_type_filter=filters,
    )
    print(f"Streaming session={args.session_id} "
          f"filter={filters or 'all'} (Ctrl-C to stop)\n")

    try:
        for frame in stub.StreamEvents(req, metadata=_metadata(args.api_key)):
            _print_frame(frame)
    except grpc.RpcError as e:
        print(f"\ngRPC error: {e.code().name} — {e.details()}")
    except KeyboardInterrupt:
        print("\nStream closed")


def cmd_history(stub, args):
    req = pb.GetSessionEventsRequest(
        session_id=args.session_id,
        start_time_sec=args.start,
        end_time_sec=args.end or -1,
    )
    count = 0
    for frame in stub.GetSessionEvents(req, metadata=_metadata(args.api_key)):
        _print_event(frame)
        count += 1
    print(f"\n{count} events")


def _print_frame(frame: "pb.MatchFrame") -> None:
    t = frame.frame_time_sec
    n_tracks = len(frame.tracks)
    evt = frame.event.type if frame.HasField("event") else ""
    ball_x = frame.ball.position.x if frame.HasField("ball") else None
    poss = frame.possession.team if frame.HasField("possession") else ""

    parts = [
        f"t={t:6.2f}s",
        f"tracks={n_tracks:2d}",
        f"poss={poss or '-':<4}",
    ]
    if ball_x is not None:
        parts.append(f"ball_x={ball_x:.2f}")
    if evt:
        parts.append(f"EVENT={evt}")
    print("  " + "  ".join(parts))


def _print_event(frame: "pb.MatchFrame") -> None:
    evt = frame.event if frame.HasField("event") else None
    if not evt:
        return
    confirmed = "CONFIRMED" if evt.confirmed else ""
    print(f"  t={frame.frame_time_sec:7.2f}s  "
          f"{evt.type:<18}  tension={evt.tension_score:4.1f}  "
          f"{confirmed}")
    if evt.description:
        print(f"             → {evt.description}")


def main():
    parser = argparse.ArgumentParser(description="Vio Vision gRPC client demo")
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help=f"gRPC server (default: {DEFAULT_TARGET})")
    parser.add_argument("--api-key", default=os.getenv("VIO_API_KEY"),
                        help="API key (optional, via x-api-key metadata)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list past sessions")
    p_list.add_argument("--limit", type=int, default=20)

    p_stream = sub.add_parser("stream", help="stream live events")
    p_stream.add_argument("session_id")
    p_stream.add_argument("--filter", action="append",
                          help="only show events of this type (repeatable)")

    p_hist = sub.add_parser("history", help="historical events")
    p_hist.add_argument("session_id")
    p_hist.add_argument("--start", type=float, default=0)
    p_hist.add_argument("--end", type=float, default=0)

    args = parser.parse_args()

    channel = grpc.insecure_channel(args.target)
    stub = pb_grpc.MatchEventsServiceStub(channel)

    cmds = {"list": cmd_list, "stream": cmd_stream, "history": cmd_history}
    cmds[args.cmd](stub, args)


if __name__ == "__main__":
    main()
