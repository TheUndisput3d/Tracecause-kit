"""Generate a lockfile inventory; licenses come from package metadata only."""

import csv
import json
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_license(package: dict[str, object]) -> tuple[str, str]:
    name = str(package["name"])
    version = str(package["version"])
    if name == "tracecause":
        return "Apache-2.0", "pyproject.toml"
    if name == "mypy-extensions" and version == "1.1.0":
        return "MIT", "https://github.com/python/mypy_extensions/blob/1.1.0/LICENSE"
    try:
        installed = metadata.distribution(name)
        if installed.version == version:
            value = installed.metadata.get("License-Expression") or installed.metadata.get(
                "License"
            )
            if value:
                return value.strip().splitlines()[0], "installed package metadata"
    except metadata.PackageNotFoundError:
        pass
    lookup = version.split("+", 1)[0]
    url = f"https://pypi.org/pypi/{name}/{lookup}/json"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            info = json.load(response)["info"]
        value = info.get("license_expression") or info.get("license")
        if value and value.strip() and len(value.strip()) < 150:
            return value.strip(), url
        classifiers = [
            item.removeprefix("License :: ")
            for item in info.get("classifiers", [])
            if item.startswith("License :: ")
        ]
        if classifiers:
            return "; ".join(classifiers), url
    except (OSError, ValueError, KeyError, urllib.error.HTTPError):
        pass
    return "UNRESOLVED", url


def main() -> None:
    packages = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    with ThreadPoolExecutor(max_workers=12) as executor:
        licenses = list(executor.map(_python_license, packages))
    with (ROOT / "docs/dependency-inventory-python.csv").open("w", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("name", "version", "license_metadata", "source"))
        for package, (license_value, source) in zip(packages, licenses, strict=True):
            writer.writerow((package["name"], package["version"], license_value, source))

    npm = json.loads((ROOT / "apps/web/package-lock.json").read_text(encoding="utf-8"))
    with (ROOT / "docs/dependency-inventory-npm.csv").open("w", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("name", "version", "license_metadata", "source"))
        for path, details in sorted(npm["packages"].items()):
            if not path:
                continue
            writer.writerow(
                (
                    path.rsplit("node_modules/", 1)[-1],
                    details.get("version", "UNRESOLVED"),
                    details.get("license", "UNRESOLVED"),
                    "package-lock.json",
                )
            )


if __name__ == "__main__":
    main()
