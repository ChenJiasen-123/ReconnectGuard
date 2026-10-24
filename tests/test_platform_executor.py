import unittest

from backends.platform_executor import PlatformExecutor
from model.contracts import BackendError, Context


class LabDouble:
    run_id = "run-1"

    def __init__(self, *, response="OK", command_error=None,
                 status_error=None, address="02:00:00:00:00:01"):
        self.response = response
        self.command_error = command_error
        self.status_error = status_error
        self.address = address
        self.health_checks = 0
        self.status_calls = 0
        self.commands = []

    def check_health(self):
        self.health_checks += 1

    def status(self):
        self.status_calls += 1
        if self.status_error:
            raise self.status_error
        return {"address": self.address}

    def command(self, target, command, semantic, allow_rejected=False):
        self.commands.append((target, command, semantic, allow_rejected))
        if self.command_error:
            raise self.command_error
        return self.response


class PlatformExecutorTest(unittest.TestCase):
    def setUp(self):
        self.context = Context("run-1", attempt_id=2)

    def test_client_actions_keep_the_existing_commands(self):
        lab = LabDouble()
        executor = PlatformExecutor(lab)

        disconnect = executor.execute("client_disconnect", self.context)
        reconnect = executor.execute("client_reconnect", self.context)

        self.assertEqual(
            [(target, command) for target, command, _, _ in lab.commands],
            [("sta", "DISCONNECT"), ("sta", "RECONNECT")],
        )
        self.assertTrue(disconnect.accepted)
        self.assertTrue(reconnect.accepted)
        self.assertEqual(disconnect.attempt_id, 2)
        self.assertEqual(reconnect.action_id, "run-1:action:2")
        self.assertEqual(lab.status_calls, 0)

    def test_ap_actions_resolve_and_validate_station_address(self):
        lab = LabDouble(address="AA:BB:CC:DD:EE:FF")
        executor = PlatformExecutor(lab)

        deauth = executor.execute("ap_deauth", self.context)
        disassoc = executor.execute("ap_disassoc", self.context)

        self.assertEqual(
            [(target, command) for target, command, _, allow_rejected in lab.commands],
            [
                ("ap", "DEAUTHENTICATE aa:bb:cc:dd:ee:ff reason=3"),
                ("ap", "DISASSOCIATE aa:bb:cc:dd:ee:ff reason=3"),
            ],
        )
        self.assertTrue(deauth.accepted)
        self.assertTrue(disassoc.accepted)
        self.assertEqual(lab.status_calls, 2)
        self.assertTrue(all(allow_rejected for _, _, _, allow_rejected in lab.commands))

    def test_invalid_station_address_fails_before_control_command(self):
        lab = LabDouble(address="not-a-mac")
        executor = PlatformExecutor(lab)

        with self.assertRaisesRegex(BackendError, "有效 STA MAC") as raised:
            executor.execute("ap_deauth", self.context)

        self.assertEqual(raised.exception.category, "invalid_target")
        self.assertEqual(lab.commands, [])

    def test_rejected_command_is_returned_without_retry(self):
        lab = LabDouble(response="FAIL")
        executor = PlatformExecutor(lab)

        receipt = executor.execute("ap_disassoc", self.context)

        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.error_category, "action_rejected")
        self.assertEqual(receipt.raw_response, "FAIL")
        self.assertEqual(len(lab.commands), 1)

    def test_timeout_is_recorded_without_retry(self):
        lab = LabDouble(command_error=TimeoutError("control timeout"))
        executor = PlatformExecutor(lab)

        receipt = executor.execute("client_disconnect", self.context)

        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.error_category, "action_timeout")
        self.assertEqual(len(lab.commands), 1)

    def test_status_control_failure_is_recorded_for_ap_action(self):
        lab = LabDouble(status_error=OSError("status unavailable"))
        executor = PlatformExecutor(lab)

        receipt = executor.execute("ap_deauth", self.context)

        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.error_category, "control_unavailable")
        self.assertIsNone(receipt.command)
        self.assertEqual(lab.commands, [])

    def test_rejects_wrong_context_and_unknown_action(self):
        lab = LabDouble()
        executor = PlatformExecutor(lab)

        with self.assertRaisesRegex(BackendError, "run_id") as wrong_context:
            executor.execute("client_disconnect", Context("other-run"))
        self.assertEqual(wrong_context.exception.category, "invalid_context")

        with self.assertRaisesRegex(BackendError, "unknown") as unknown:
            executor.execute("unknown", self.context)
        self.assertEqual(unknown.exception.category, "unsupported_action")

        self.assertEqual(lab.commands, [])


if __name__ == "__main__":
    unittest.main()
