import unittest

from notifications.registry import get_sender, register
from notifications.service import send_notification


class NotificationTests(unittest.TestCase):
    def test_existing_channels_are_registered(self):
        self.assertEqual(send_notification("email", "a@b.test", "hello"), "email:a@b.test:hello")
        self.assertEqual(send_notification("sms", "+1000", "hello"), "sms:+1000:hello")

    def test_custom_channel_uses_registry(self):
        register("push", lambda recipient, message: f"push:{recipient}:{message}")
        self.assertEqual(send_notification("push", "device", "hello"), "push:device:hello")
        self.assertTrue(callable(get_sender("push")))

    def test_unknown_channel_error_is_preserved(self):
        with self.assertRaisesRegex(ValueError, "unknown notification channel: fax"):
            send_notification("fax", "x", "hello")


if __name__ == "__main__":
    unittest.main()
