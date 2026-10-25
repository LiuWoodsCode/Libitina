#!/usr/bin/env python3
"""Desktop Notifications protocol playground for Phosh and Yui."""
import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))
from playground import GLib, Playground, run


class NotifyTester(Playground):
    def __init__(self, app):
        super().__init__(app, "NotifyTester", "org.freedesktop.Notifications",
                         "/org/freedesktop/Notifications")
        self.name = self.field("Application name", "NotifyTester")
        self.summary = self.field("Summary", "Hello from Libitina")
        self.message = self.field("Body (server-supported markup allowed)",
                                  "A notification API test")
        self.icon = self.field("Icon name or absolute path", "dialog-information")
        self.replace_id = self.field("Replace ID (0 creates a new notification)", "0")
        self.timeout = self.field("Timeout in milliseconds (-1 default, 0 persistent)", "-1")
        self.actions = self.field("Actions: JSON array of key, label pairs",
                                  '["default", "Open", "test", "Test action"]')
        self.hint_entry = self.field("Hints: typed GVariant a{sv}",
                                    "{'urgency': <byte 1>, 'desktop-entry': <'libitina-NotifyTester'>}")
        self.button("Send notification", self.send)
        self.close_id = self.field("Notification ID to close", "0")
        self.button("Close notification", self.close_notification)
        self.button("Inspect server and capabilities", self.inspect)
        self.start()

    def inspect(self):
        self.call("GetServerInformation")
        self.call("GetCapabilities")

    def send(self):
        actions = json.loads(self.actions.get_text())
        if (not isinstance(actions, list) or len(actions) % 2
                or not all(isinstance(item, str) for item in actions)):
            raise ValueError("Actions must be a JSON array of string key/label pairs")
        hints = self.hints(self.hint_entry)
        parameters = GLib.Variant.new_tuple(
            GLib.Variant("s", self.name.get_text()),
            GLib.Variant("u", int(self.replace_id.get_text())),
            GLib.Variant("s", self.icon.get_text()),
            GLib.Variant("s", self.summary.get_text()),
            GLib.Variant("s", self.message.get_text()),
            GLib.Variant("as", actions), hints,
            GLib.Variant("i", int(self.timeout.get_text())))
        self.call("Notify", parameters, self.sent)

    def sent(self, reply):
        self.close_id.set_text(str(reply[0]))
        self.log(f"Use ID {reply[0]} in Replace ID to update this notification.")

    def close_notification(self):
        self.call("CloseNotification", GLib.Variant("(u)", (int(self.close_id.get_text()),)))


if __name__ == "__main__":
    raise SystemExit(run("io.github.pixelprowler.NotifyTester", NotifyTester))
