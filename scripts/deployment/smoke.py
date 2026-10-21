"""Actual same-origin public replay smoke; optional CA validates local TLS."""

import argparse
import hashlib
import json
import ssl
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:13000")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify = ssl.create_default_context(cafile=str(args.ca)) if args.ca else True
    with httpx.Client(base_url=args.url, verify=verify, trust_env=False, timeout=15) as client:
        landing = client.get("/")
        landing.raise_for_status()
        assert '<div id="root">' in landing.text
        assert landing.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in landing.headers["content-security-policy"]
        assert client.get("/health/ready").json()["status"] == "ready"
        config = client.get("/api/v1/config").json()
        assert config["public_read_only"] is True and config["mutations_enabled"] is False
        episodes = client.get("/api/v1/episodes").json()["items"]
        assert episodes
        episode = episodes[0]
        episode_id = episode["episode_id"]
        scope = dict(
            episode_id=episode_id,
            snapshot_id=episode["active_snapshot_id"],
            as_of=episode["event_interval"]["end"],
            temporal_mode="event_time_replay",
        )
        graph = client.get("/api/v1/graph", params=scope)
        graph.raise_for_status()
        assert graph.json()["nodes"] and graph.json()["edges"]
        views = client.get(f"/api/v1/episodes/{episode_id}/investigations").json()["items"]
        assert views
        result = None
        identifier = None
        for view in views:
            saved = client.get(f"/api/v1/investigations/{view['investigation_id']}")
            saved.raise_for_status()
            candidate = saved.json()["result"]
            if candidate is not None and candidate["generation_origin"] == "template":
                result = candidate
                identifier = view["investigation_id"]
                break
        assert result is not None and identifier is not None
        assert result["is_replayed"] is True and result["generation_origin"] == "template"
        assert result["provenance"]["model_digest"] is None
        assert result["evidence"]
        for evidence in result["evidence"]:
            opened = client.get("/api/v1/evidence/" + evidence["evidence_id"], params=scope)
            opened.raise_for_status()
            assert opened.json()["text"] == evidence["text"]
        token = Path(".secrets/release/operator_token").read_text().strip()
        denied = {}
        for path in ("/api/v1/imports", "/api/v1/investigations", "/api/chat"):
            status = client.post(
                path, json={}, headers={"Authorization": "Bearer " + token}
            ).status_code
            assert status == 403
            denied[path] = status
        private = {}
        for path in (
            "/internal/metrics",
            "/docs",
            "/openapi.json",
            "/data/gold/index.json",
            "/.env",
        ):
            status = client.get(path).status_code
            assert status == 404
            private[path] = status
        report = {
            "url": args.url,
            "tls_ca_verified": args.ca is not None,
            "public_internet_deployment": False,
            "episode_id": episode_id,
            "snapshot_id": scope["snapshot_id"],
            "investigation_id": identifier,
            "citations_resolved": len(result["evidence"]),
            "saved_template_replay": True,
            "result_sha256": hashlib.sha256(
                json.dumps(result, sort_keys=True).encode()
            ).hexdigest(),
            "mutations_denied": denied,
            "private_paths": private,
            "generation_provider_required": False,
            "security_headers": dict(landing.headers),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
