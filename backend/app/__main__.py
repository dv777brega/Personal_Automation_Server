"""Convenient, local-only default launch command."""
import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Personal Automation Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("PAS_API_TOKEN"):
        parser.error("Set PAS_API_TOKEN before listening on a network interface")
    uvicorn.run("backend.app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
