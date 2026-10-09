#!/usr/bin/env python3
"""Serve the Windows Bluetooth client without starting a local Pi host."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5058)
    parser.add_argument("--state", type=Path, help="DPAPI-encrypted client state file")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This client currently supports Windows only.")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    from cheroot.wsgi import Server
    from wireless.host_lock import database_process_lock
    from wireless.pc_client_app import create_pc_client_app
    from wireless.pc_client_store import PcClientStore

    root = Path(__file__).resolve().parent
    if not (root / "dist" / "index.html").is_file():
        parser.error("Build the dashboard first with npm run build.")
    state_path = PcClientStore(args.state).path
    with database_process_lock(state_path):
        app = create_pc_client_app(
            port=args.port, dist_root=root / "dist", state_path=state_path,
        )
        server = Server(("127.0.0.1", args.port), app, numthreads=8, timeout=120)
        print(f"BASS PC Bluetooth: http://127.0.0.1:{args.port}/pc-client", flush=True)
        try:
            server.start()
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
            app.extensions["bass_pc_client"].close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
