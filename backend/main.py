from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

try:
    from interfaces.api.main import app
except ImportError:
    # Compatibility entrypoint for old backend-based commands.
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from interfaces.api.main import app


# %%
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8510)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        client = TestClient(app)
        response = client.get("/api/profile", headers={"x-profile-ip": "127.0.0.1"})
        assert response.status_code == 200
        print("Check Passed")
    else:
        uvicorn.run("interfaces.api.main:app", host="0.0.0.0", port=args.port, reload=True)
