"""Host-only exact source packing and VMware queue preparation. Holds no credentials."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"pr05b-dependency-[A-Za-z0-9-]+", args.run_id):
        raise SystemExit("invalid run id")
    root = Path(__file__).resolve().parents[2]
    broker = Path(r"J:\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\vmware-broker")
    output = root / "outputs/pr05b/dependency" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).decode("utf-8").split("\0")
    files = sorted(name for name in set(names) if name and (root / name).is_file() and not name.startswith("web/backend/migrations/versions/0012_"))
    manifest = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    with zipfile.ZipFile(output / "source.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(root / name, name)
    source_hash = hashlib.sha256((output / "source.zip").read_bytes()).hexdigest()
    archive_path = broker / "scripts" / (args.run_id + ".zip")
    archive_path.write_bytes((output / "source.zip").read_bytes())
    script_path = broker / "scripts" / (args.run_id + ".ps1")
    script = r"""$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware') { throw 'VMware guest required' }
if (-not (Test-Path 'J:\')) { & subst.exe J: C:\j | Out-Null }
$root = 'J:\class-assistant\worktrees\pr05b-runs\RUN_ID'
if (Test-Path -LiteralPath $root) { throw 'Run already exists' }
New-Item -ItemType Directory -Path $root -Force | Out-Null
Copy-Item -LiteralPath '\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\scripts\RUN_ID.zip' -Destination "$root\source.zip"
if ((Get-FileHash "$root\source.zip" -Algorithm SHA256).Hash -ne 'SOURCE_SHA') { throw 'Source hash mismatch' }
Expand-Archive -LiteralPath "$root\source.zip" -DestinationPath "$root\source"
$dependency = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$python = "$dependency\work\pr03b\product-completion-20261110-01\venv\Scripts\python.exe"
$pgbin = "$dependency\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin"
$env:PR05A_VMWARE_GUEST = '1'
$env:PR05A_DEPENDENCY_ROOT = $dependency
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = "$root\temp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
"run=RUN_ID source_sha256=SOURCE_SHA guest=$env:COMPUTERNAME manufacturer=$($machine.Manufacturer)"
& $python -B "$root\source\tools\pr05b\run_dependency.py" --pg-bin $pgbin --output "$root\evidence"
$code = $LASTEXITCODE
if (Test-Path "$root\evidence") {
    $files = @(Get-ChildItem "$root\evidence" -File)
    if ($files.Count) { Compress-Archive -LiteralPath $files.FullName -DestinationPath "$root\evidence.zip" }
}
"guest_exit=$code"
exit $code
""".replace("RUN_ID", args.run_id).replace("SOURCE_SHA", source_hash)
    script_path.write_text(script, encoding="utf-8-sig")
    receipt = dict(run_id=args.run_id, source_sha256=source_hash, files_sha256=manifest,
                   bootstrap=str(script_path), source_root=str(root), candidate_only=True)
    (output / "snapshot.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_id": args.run_id, "source_sha256": source_hash, "files": len(files), "bootstrap": str(script_path)}))


if __name__ == "__main__":
    main()
