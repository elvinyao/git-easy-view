"""Explicitly load generated local passwords without exposing them in argv."""
import json
from pathlib import Path
from app.config import Settings
from app.demo import create_demo

access = json.loads(Path("/workspace/runtime/demo-access.json").read_text())
create_demo(Settings(), admin_password=access["admin"]["password"], member_password=access["member"]["password"])
print("Local example created. Credentials are in runtime/demo-access.json.")
