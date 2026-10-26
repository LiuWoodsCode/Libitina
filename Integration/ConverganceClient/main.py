
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkVnc", "2.0")

from gi.repository import Gtk, GtkVnc


class VNCViewer(Gtk.Window):
    def __init__(self, host="172.16.42.1", port=5900):
        super().__init__(title="PyGObject VNC Viewer")

        self.set_default_size(1024, 768)
        self.connect("destroy", Gtk.main_quit)

        # Create the VNC display widget
        self.display = GtkVnc.Display()

        # Enable keyboard and mouse input
        self.display.set_read_only(False)

        # Scale the remote desktop to fit the window
        self.display.set_scaling(True)

        # Add display to the GTK window
        self.add(self.display)

        # Connect VNC signals
        self.display.connect("vnc-connected", self.on_connected)
        self.display.connect("vnc-disconnected", self.on_disconnected)
        self.display.connect("vnc-auth-credential", self.on_auth_credential)

        # Show the window
        self.show_all()

        # Connect to the VNC server
        self.display.open_host(host, str(port))

    def on_connected(self, display):
        print("Connected to VNC server!")

    def on_disconnected(self, display):
        print("Disconnected from VNC server.")

    def on_auth_credential(self, display, credentials):
        print("VNC authentication required:", credentials)


if __name__ == "__main__":
    viewer = VNCViewer("127.0.0.1", 5900)
    Gtk.main()
