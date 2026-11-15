"""Run non-schema checks locally; full discovery remains the mandatory gate.

This helper never reports contract validation as passed. It is useful in the
restricted worker without jsonschema; the controller must run full discovery.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'web/backend/tests/asr_sim'))
from test_simulator import SimulatorTests

if __name__ == '__main__':
    print('SIMULATION ONLY: stdlib subset; schema gate NOT RUN', flush=True)
    names = unittest.defaultTestLoader.getTestCaseNames(SimulatorTests)
    suite = unittest.TestSuite(SimulatorTests(name) for name in names
                              if name != 'test_contract_payloads_and_format_checker')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
