"""Compare two private transcript files without printing their contents."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "web" / "backend"))

from classroom_web.pr06a_real_poc.policy import compare_transcript


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("actual", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--allow-prefix", action="store_true")
    args = parser.parse_args()
    actual = args.actual.read_text(encoding="utf-8-sig")
    reference = args.reference.read_text(encoding="utf-8-sig")
    print(compare_transcript(actual, reference, allow_prefix=args.allow_prefix))


if __name__ == "__main__":
    main()
