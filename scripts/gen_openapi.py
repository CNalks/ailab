"""Write docs/openapi.json from the app (no server needed). Run: `make openapi`."""

import json
from pathlib import Path

from ailab.api.app import create_app
from ailab.api.settings import Settings

spec = create_app(settings=Settings()).openapi()
out = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
out.write_text(json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
print(f"wrote {out} ({len(spec['paths'])} paths)")
