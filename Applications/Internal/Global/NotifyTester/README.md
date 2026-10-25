# NotifyTester

A GTK 3 playground for `org.freedesktop.Notifications`, usable with Phosh,
Yui's notification server, and other implementations of the desktop notification
specification. Requires Python 3, PyGObject, GTK 3, and a Linux graphical session
with a session D-Bus and notification server. On Debian/Mobian install
`python3-gi gir1.2-gtk-3.0`.

Install with `python3 Applications/install.py --kind Internal --environment Global`
from the repository root, or run `./launch.sh` from this directory.

Send a notification, then copy its returned ID from the log or close field into
Replace ID to update it. Use the close field to remove a notification. Server
information, capabilities, action invocations, closure reasons, and activation
tokens appear in the log. Signals shown by the playground can include notifications
from other clients; IDs identify the relevant notification.

Actions use a JSON array of alternating keys and labels. Hints use typed GVariant
syntax, for example:

```
{'urgency': <byte 2>, 'resident': <true>, 'category': <'im.received'>}
{'image-path': <'/absolute/path/image.png'>, 'suppress-sound': <true>}
```

Timeouts are milliseconds: -1 uses the server default and 0 requests no expiry.
The server determines which hints, markup, actions, and timeout requests it honors.
This app exercises the raw desktop notification protocol, not Gio.Notification
or the notification portal.

Protocol reference: https://specifications.freedesktop.org/notification/latest/protocol.html
