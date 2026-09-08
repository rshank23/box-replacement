"""Generate the binary demo fixtures (nested and corrupt ZIP archives)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = REPO_ROOT / "demo" / "mbox" / "STUDY-003" / "JP" / "SITE-301" / "Site Management"


def build_nested_zip(path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "signature-sheet.pdf",
            "Demo document (not a real PDF).\nStudy: STUDY-003 | Country: JP | Site: SITE-301\n"
            "Expected outcome: SUCCESS after two levels of ZIP extraction.\n",
        )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("delegation-log.pdf", "Demo document (not a real PDF).\nSite delegation log.\n")
        zf.writestr("level-2/inner.zip", inner.getvalue())
    print(f"wrote {path}")


def build_corrupt_zip(path: Path) -> None:
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 32 + b"this-is-not-a-valid-central-directory")
    print(f"wrote {path}")


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    build_nested_zip(TARGET_DIR / "site-docs.zip")
    build_corrupt_zip(TARGET_DIR / "corrupt.zip")


if __name__ == "__main__":
    main()
