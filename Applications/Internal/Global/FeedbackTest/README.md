# FeedbackTest

A GTK 3 playground for feedbackd's `org.sigxcpu.Feedback` session D-Bus API.
Requires Python 3, PyGObject, GTK 3, a Linux graphical session, and feedbackd with
an installed feedback theme. On Debian/Mobian install
`python3-gi gir1.2-gtk-3.0 feedbackd`. libfeedback introspection bindings are not
required because the app calls the daemon directly.

Install with `python3 Applications/install.py --kind Internal --environment Global`
from the repository root, or run `./launch.sh` from this directory.

Enter an event such as `message-new-instant`, `phone-incoming-call`, or
`button-pressed` and trigger it. Event availability and actual sound, vibration,
or LED behavior depend on the installed theme, active profile, and hardware.
The returned ID fills the stop field; copy other IDs from the log to stop them.
`FeedbackEnded` signals and their numeric reasons appear in the log. Signals can
include events triggered by other clients. The bus connection stays open while
the app runs; feedbackd stops this client's feedback when it disconnects.

Timeouts are seconds: -1 uses the event's natural duration, 0 loops until stopped,
and a positive value limits the duration. Hints use typed GVariant syntax:

```
{}
{'profile': <'quiet'>}
{'profile': <'full'>, 'important': <true>}
```

Important feedback can be restricted by the daemon's allow-important policy.
Set global profile changes the session-wide daemon profile and does not restore
it automatically. Use Inspect current profile before changing it if you want to
restore the previous value afterward. For a single event, prefer a profile hint.

Upstream: https://gitlab.freedesktop.org/feedbackd/feedbackd
API reference: https://honk.sigxcpu.org/projects/feedbackd/doc/method.GdbusFeedback.call_trigger_feedback.html
