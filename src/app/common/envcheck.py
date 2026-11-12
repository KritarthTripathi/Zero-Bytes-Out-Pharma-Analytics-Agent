"""Pinned-environment check. `scripts/pin_environment.py` records the installed package versions, the model digests reported by
Ollama and the hashes of the configuration and evaluation files. At startup `verify_environment()` compares the running system with
that manifest. ENV_CHECK=off | warn (default) | strict (refuse to start on drift)."""
import hashlib
import json
import os
import urllib.request
from pathlib import Path

MANIFEST = Path(os.getenv("ENV_MANIFEST", "data/environment/manifest.json"))
TRACKED_FILES = ["src/app/sales/semantic/metrics.yml", "src/app/sales/semantic/agents.yml", "src/app/sales/semantic/joins.yml",
                 "src/app/sales/semantic/joins_overrides.yml", "src/app/sales/semantic/schema.sql", "src/app/sales/semantic/views.sql",
                 "data/evals/sales_golden.yaml", "data/evals/sales_holdout.yaml", "data/evals/routing_dev.yaml", "data/evals/routing_holdout.yaml"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packages() -> dict:
    from importlib import metadata
    return {d.metadata["Name"].lower(): d.version for d in metadata.distributions() if d.metadata["Name"]}


def _ollama_models(url: str | None = None) -> dict:
    url = (url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=5) as r:
            return {m["name"]: m.get("digest", "") for m in json.load(r).get("models", [])}
    except Exception:
        return {}


def current_state() -> dict:
    import platform
    return {"python": platform.python_version(), "packages": _packages(), "models": _ollama_models(),
            "files": {f: _sha(Path(f)) for f in TRACKED_FILES if Path(f).exists()}}


def manifest_hash(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def compare(manifest: dict, now: dict) -> list[str]:
    """Differences between the pinned manifest and the running system (pure function)."""
    diffs = []
    if manifest.get("python") != now.get("python"):
        diffs.append(f"python {manifest.get('python')} -> {now.get('python')}")
    for k, v in manifest.get("packages", {}).items():
        if k not in now.get("packages", {}):
            diffs.append(f"package {k} missing")
        elif now["packages"][k] != v:
            diffs.append(f"package {k} {v} -> {now['packages'][k]}")
    for k, v in manifest.get("models", {}).items():
        if now.get("models") and now["models"].get(k) != v:
            diffs.append(f"model {k} digest changed" if k in now["models"] else f"model {k} missing")
    for k, v in manifest.get("files", {}).items():
        if now.get("files", {}).get(k) != v:
            diffs.append(f"file {k} changed (re-run the evaluations)")
    return diffs


def verify_environment(mode: str | None = None) -> dict:
    mode = mode or os.getenv("ENV_CHECK", "warn")
    if mode == "off":
        return {"status": "off", "differences": [], "manifest_hash": None}
    if not MANIFEST.exists():
        return {"status": "no_manifest", "differences": [], "manifest_hash": None}
    manifest = json.loads(MANIFEST.read_text())
    diffs = compare(manifest, current_state())
    res = {"status": "ok" if not diffs else "drift", "differences": diffs[:20], "manifest_hash": manifest_hash(manifest)}
    if diffs and mode == "strict":
        raise RuntimeError("environment differs from the pinned manifest: " + "; ".join(diffs[:5]))
    return res
