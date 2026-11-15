import io
import json
import unittest
import zipfile
from tools.overnight.queue import decode_request, find_request
from tools.overnight.state import StateError


def zipped(name="request.json", extra=False, **changes):
    value = dict(version=1, task="06a", simulation_only=True,
                 repository="Mumao233/classroom-os", base_sha="a" * 40, request_id="123")
    value.update(changes)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, json.dumps(value))
        if extra:
            archive.writestr("command.sh", "untrusted")
    return output.getvalue()


class QueueTests(unittest.TestCase):
    def test_only_data_for_exact_authorized_sha(self):
        self.assertEqual(decode_request(zipped(), "a" * 40)["task"], "06a")
        for data in [zipped(name="../request.json"), zipped(extra=True), zipped(task="06b"),
                     zipped(base_sha="b" * 40), zipped(command="exec arbitrary")]:
            with self.assertRaises(StateError):
                decode_request(data, "a" * 40)

    def test_other_actor_or_workflow_is_not_a_request(self):
        class Client:
            def get(self, _):
                return {"workflow_runs": [{"head_sha": "a" * 40, "actor": {"login": "attacker"},
                    "event": "push", "path": ".github/workflows/overnight-queue.yml"}]}
        self.assertIsNone(find_request(Client(), "a" * 40))


if __name__ == "__main__":
    unittest.main()
