"""The project version is declared in two files. This keeps them honest.

server/gep/__init__.py and client/package.json are separate ecosystems with no
shared manifest, so nothing but a test stops one from being bumped without the
other. A mismatch is silent otherwise, and "which version is this?" stops
having one answer.
"""
import json
import pathlib
import re

import gep

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_server_version_is_a_valid_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", gep.__version__), gep.__version__


def test_client_and_server_versions_match():
    package_json = json.loads((REPO / "client" / "package.json").read_text(encoding="utf-8"))
    assert package_json["version"] == gep.__version__, (
        f"client/package.json is {package_json['version']!r} but "
        f"gep.__version__ is {gep.__version__!r} -- bump both"
    )
