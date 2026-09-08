"""Build the POC folders: source fixtures (including nested and corrupt ZIPs) plus empty sinks.

    python scripts/make_poc_data.py
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POC = REPO_ROOT / "poc"
SOURCE = POC / "source"


def build_nested_zip(target: Path) -> None:
    """Three levels: outer.zip -> level-2/inner.zip -> signature-sheet.pdf."""
    innermost = io.BytesIO()
    with zipfile.ZipFile(innermost, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "signature-sheet.pdf",
            "Site signature sheet\nStudy: STUDY-003 | Country: JP | Site: SITE-301\n"
            "Expected: extracted from two levels of ZIP and filed into\n"
            "destination/STUDY-003/JP/SITE-301/Site Management/Site Signature Sheet/\n",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "delegation-log.pdf",
            "Site delegation log\nStudy: STUDY-003 | Country: JP | Site: SITE-301\n"
            "Expected: extracted from the outer ZIP and filed to the destination.\n",
        )
        zf.writestr("level-2/inner.zip", innermost.getvalue())
    print(f"  nested zip   {target.relative_to(REPO_ROOT)}")


def build_corrupt_zip(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PK\x03\x04" + b"\x00" * 32 + b"truncated-central-directory")
    print(f"  corrupt zip  {target.relative_to(REPO_ROOT)}")


def main() -> None:
    if not SOURCE.exists():
        print(f"Source folder {SOURCE} is missing; check out the repository fixtures first.", file=sys.stderr)
        raise SystemExit(1)

    print("POC fixtures:")
    build_nested_zip(SOURCE / "STUDY-003/JP/SITE-301/Site Management/site-docs.zip")
    build_corrupt_zip(SOURCE / "STUDY-003/JP/SITE-301/Site Management/corrupt.zip")

    for name in ("destination", "unclassified"):
        folder = POC / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".gitkeep").touch()
        print(f"  sink         {folder.relative_to(REPO_ROOT)}")

    files = sorted(p for p in SOURCE.rglob("*") if p.is_file())
    print(f"\n{len(files)} file(s) staged in {SOURCE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
