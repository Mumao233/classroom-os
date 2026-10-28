"""Verify every supplied baseline file byte-for-byte, including frozen contracts."""
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
BASE = "1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    entries = git("ls-tree", "-r", "-z", BASE).split(b"\0")
    changed = []
    count = 0
    for entry in entries:
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        _, kind, expected = header.split()
        if kind != b"blob":
            raise RuntimeError("unexpected non-file baseline entry")
        relative = name.decode("utf-8")
        file = ROOT / relative
        actual = git("hash-object", "--path=" + relative, "--", str(file)).strip() if file.is_file() else b"missing"
        if actual != expected:
            changed.append(relative)
        count += 1
    # Also verify the binary PRD's published archive checksum, without opening private data.
    prd = ROOT / "docs/web-alpha/Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2.pdf"
    prd_sha = hashlib.sha256(prd.read_bytes()).hexdigest()
    expected_prd = (ROOT / "docs/web-alpha/SHA256SUMS.txt").read_text("utf-8").split()[0].lower()
    result = {"base": BASE, "baseline_files_checked": count, "changed": changed,
              "prd_sha256": prd_sha, "passed": not changed and prd_sha == expected_prd}
    output = ROOT / "outputs/pr02a/protected-files.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
