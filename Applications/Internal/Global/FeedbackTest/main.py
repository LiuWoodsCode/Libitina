#!/usr/bin/env python3
"""feedbackd event API playground without a libfeedback dependency."""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))
from playground import GLib, Playground, run


class FeedbackTest(Playground):
    def __init__(self, app):
        super().__init__(app, "FeedbackTest", "org.sigxcpu.Feedback",
                         "/org/sigxcpu/Feedback")
        self.app_id = self.field("Feedback application ID", "libitina-FeedbackTest")
        self.event = self.field("Event name (editable; theme determines feedback)",
                                "message-new-instant")
        self.timeout = self.field("Timeout in seconds (-1 theme default, 0 loop)", "-1")
        self.hint_entry = self.field("Hints: typed GVariant a{sv}", "{}")
        self.button("Trigger feedback", self.trigger)
        self.event_id = self.field("Feedback ID to stop (filled after triggering)", "0")
        self.button("Stop feedback ID", self.stop)
        self.button("Inspect current profile", self.inspect)
        self.profile = self.field("Global profile: full, quiet, or silent", "full")
        self.button("Set global profile", self.set_profile)
        self.start()

    def inspect(self):
        self.call("Get", GLib.Variant("(ss)", (self.service, "Profile")),
                  interface="org.freedesktop.DBus.Properties")

    def trigger(self):
        event = self.event.get_text().strip()
        app_id = self.app_id.get_text().strip()
        if not event or not app_id:
            raise ValueError("Application ID and event name are required")
        timeout = int(self.timeout.get_text())
        if timeout < -1:
            raise ValueError("Timeout must be -1, 0, or a positive number of seconds")
        parameters = GLib.Variant.new_tuple(
            GLib.Variant("s", app_id), GLib.Variant("s", event),
            self.hints(self.hint_entry), GLib.Variant("i", timeout))
        self.call("TriggerFeedback", parameters,
                  lambda reply: self.event_id.set_text(str(reply[0])))

    def stop(self):
        self.call("EndFeedback", GLib.Variant("(u)", (int(self.event_id.get_text()),)))

    def set_profile(self):
        profile = self.profile.get_text().strip()
        if profile not in ("full", "quiet", "silent"):
            raise ValueError("Profile must be full, quiet, or silent")
        self.call("Set", GLib.Variant("(ssv)",
                  (self.service, "Profile", GLib.Variant("s", profile))),
                  lambda _: self.inspect(), interface="org.freedesktop.DBus.Properties")


if __name__ == "__main__":
    raise SystemExit(run("io.github.pixelprowler.FeedbackTest", FeedbackTest))
