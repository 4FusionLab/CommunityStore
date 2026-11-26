"""Build the bilingual Pages artifact with a fresh default Marketplace count."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil

from CommunityStore import releases, trusted_stores


ROOT = Path(__file__).resolve().parents[1]


def catalog_stats(owners):
    seen = set()
    count = 0
    stars = {}
    for owner in owners:
        stars[owner] = 0
        for source in releases.public_repositories(owner):
            key = source["repo"].casefold()
            if key in seen:
                continue
            seen.add(key)
            entry = releases.latest(source)
            if entry.get("tag") and entry.get("isAddin"):
                count += 1
                stars[owner] += source["stars"]
    return count, stars


def build(destination):
    # Finish discovery before creating any publishable output. GitHub/network
    # errors propagate so Pages keeps the last successful deployment.
    token = os.environ.get("GITHUB_TOKEN")
    session = releases.GitHubSession(token) if token else None
    with releases.use_session(session):
        directory = json.loads((ROOT / trusted_stores.FILENAME).read_text(encoding="utf-8"))
        count, stars = catalog_stats(trusted_stores.parse(directory))
    shutil.copytree(ROOT / "docs/pages", destination)
    (destination / "author-stars.js").write_text(
        "const catalogAuthorStars = " + json.dumps(stars, indent=2) + ";\n", encoding="utf-8"
    )
    for page in ("index.html", "ru/index.html"):
        path = destination / page
        html, changes = re.subn(
            r'(<strong data-catalog-count>)\d+(</strong>)',
            lambda match: f"{match[1]}{count}{match[2]}",
            path.read_text(encoding="utf-8"),
        )
        if changes != 1:
            raise ValueError(f"Expected exactly one catalog count in {page}")
        path.write_text(html, encoding="utf-8")
    print(f"Built Pages with {count} add-ins in the default catalog.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    build(parser.parse_args().destination)
