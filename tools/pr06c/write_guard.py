"""Materialize the existing PR-02b loopback guard without running its suite."""
import ast
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
tree = ast.parse((root/'tools/pr02b/run_tests.py').read_text(encoding='utf8'))
assignment, = (node for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'NETWORK_GUARD' for target in node.targets))
Path(sys.argv[1]).write_text(ast.literal_eval(assignment.value), encoding='utf8')
