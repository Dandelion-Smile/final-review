"""Package only the standalone UI demo for static hosting."""

from pathlib import Path
import argparse
import shutil
import zipfile


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=repository / "dist" / "ui-v2-demo")
    args = parser.parse_args()
    source = repository / "docs" / "ui-v2"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    files = {
        "prototype.html": "index.html",
        "prototype.css": "prototype.css",
        "prototype.js": "prototype.js",
        "mock-data.js": "mock-data.js",
    }
    # An explicit allowlist excludes the backend, local data and design documents.
    for source_name, destination_name in files.items():
        shutil.copyfile(source / source_name, output / destination_name)
    (output / ".nojekyll").write_text("", encoding="utf-8")

    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for filename in [*files.values(), ".nojekyll"]:
            package.write(output / filename, filename)

    print(f"Site: {output}")
    print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
