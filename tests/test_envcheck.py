from app.common.envcheck import compare, manifest_hash

M = {"python": "3.11.9", "packages": {"duckdb": "1.1.0", "sqlglot": "25.0.0"}, "models": {"qwen2.5:3b": "abc"}, "files": {"metrics.yml": "h1"}}


def test_identical_environment_has_no_differences():
    assert compare(M, dict(M)) == []


def test_drift_is_reported():
    now = {"python": "3.11.9", "packages": {"duckdb": "1.2.0"}, "models": {"qwen2.5:3b": "zzz"}, "files": {"metrics.yml": "h2"}}
    d = " | ".join(compare(M, now))
    assert "duckdb 1.1.0 -> 1.2.0" in d and "sqlglot missing" in d and "digest changed" in d and "metrics.yml changed" in d


def test_manifest_hash_is_stable():
    assert manifest_hash(M) == manifest_hash(dict(reversed(list(M.items()))))
