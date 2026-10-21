"""Compare running Docker Python and web bytes with the reviewed local build."""

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from scripts.deployment.full_lifecycle import COMPOSE, boundaries


def hashes(root: Path, pattern: str = "*") -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob(pattern))
        if path.is_file()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be new")
    remote = """
import hashlib,json,tracecause
from pathlib import Path
root=Path(tracecause.__file__).parent
print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(root.rglob('*.py'))}))
"""
    expected = hashes(Path("src/tracecause"), "*.py")
    for service in ("api", "worker"):
        actual = json.loads(
            subprocess.check_output(
                [*COMPOSE, "exec", "-T", service, "python", "-c", remote], text=True
            )
        )
        if actual != expected:
            raise ValueError(f"{service} runtime source differs from reviewed worktree")
    web_id = subprocess.check_output([*COMPOSE, "ps", "-q", "web"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="tracecause-web-audit-") as directory:
        subprocess.run(["docker", "cp", web_id + ":/srv/.", directory], check=True)
        web = hashes(Path(directory))
        if web != hashes(Path("apps/web/dist")):
            raise ValueError("served frontend differs from local production build")
    report = dict(
        passed=True,
        python_files=expected,
        web_files=web,
        containers=boundaries(),
        models={
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path("models").glob("candidate-logistic-v*/model.json")
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(passed=True, python_files=len(expected), web_files=len(web))))


if __name__ == "__main__":
    main()
