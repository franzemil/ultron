#!/usr/bin/env python3
"""Insert a pending dated section into CHANGELOG.md.

Called by .github/workflows/changelog.yml. The changelog is auto-generated:
this script inserts the new "## <date>" section (built by the workflow at
/tmp/section.md) after the file header and before the most recent existing
section. No-op when the section has no PR bullets.
"""
import sys
from pathlib import Path


def main() -> int:
    section_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/section.md")
    section = section_path.read_text(encoding="utf-8").strip("\n")

    if not any(line.startswith("- ") for line in section.splitlines()):
        print("No PR entries in section — CHANGELOG.md left untouched.")
        return 0

    p = Path("CHANGELOG.md")
    if p.exists() and p.read_text(encoding="utf-8").startswith("# Changelog"):
        content = p.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n"

    block = "\n" + section + "\n"
    idx = content.find("\n## ")
    if idx == -1:
        p.write_text(content.rstrip("\n") + "\n" + block + "\n", encoding="utf-8")
    else:
        p.write_text(content[:idx] + block + content[idx:], encoding="utf-8")
    print("CHANGELOG.md updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
