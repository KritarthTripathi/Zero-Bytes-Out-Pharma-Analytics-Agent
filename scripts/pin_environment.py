"""Records the pinned environment (python, package versions, Ollama model digests, hashes of configuration and evaluation files).
Run once the system works as intended, and again after any deliberate change:  python scripts/pin_environment.py"""
import json

from app.common.envcheck import MANIFEST, current_state, manifest_hash

state = current_state()
if not state["models"]:
    print("WARNING: no Ollama models found (is the ollama service running?). The manifest will not pin model digests.")
MANIFEST.parent.mkdir(parents=True, exist_ok=True)
MANIFEST.write_text(json.dumps(state, indent=1, sort_keys=True))
print(f"pinned {len(state['packages'])} packages, {len(state['models'])} models {sorted(state['models'])}, {len(state['files'])} files")
print("manifest:", MANIFEST, "| hash:", manifest_hash(state)[:16])
