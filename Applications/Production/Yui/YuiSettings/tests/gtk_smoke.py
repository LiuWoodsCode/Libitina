"""Run with GTK, libhandy, and a display. All system services use fixtures."""

import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory(prefix='yui-settings-smoke-')
os.environ['GSETTINGS_BACKEND'] = 'memory'
os.environ['XDG_CONFIG_HOME'] = temporary.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Handy', '1')
from gi.repository import Gtk, Handy, GLib, Gio
import service_page
import main
import network_pages
import system_pages
import bluetooth_page
import connection_page
import display_page

Handy.init()
network = dict(state=['connected', 'full', 'enabled', 'enabled', 'enabled', 'enabled', 'enabled'],
    devices=[['wlan0', 'wifi', 'connected', 'Home'], ['eth0', 'ethernet', 'connected', 'Wired connection']],
    profiles=[['wifi-uuid', 'Home', '802-11-wireless', 'wlan0'], ['vpn-uuid', 'VPN', 'vpn', '--'], ['wired-uuid', 'Wired connection', '802-3-ethernet', 'eth0']],
    details='GENERAL.DEVICE: wlan0\nIP4.ADDRESS[1]: 192.168.1.2/24\nIP4.DNS[1]: 1.1.1.1')
wifi = dict(network, adapters=[['wlan0','wifi','connected','Home']], interface='wlan0',
    access_points=[['*','Home','00:11:22:33:44:55','90','WPA2','/ap/1'],
                   ['','Cafe: west\\east','00:11:22:33:44:56','80','WPA3','/ap/2'], ['','Guest','00:11:22:33:44:57','65','','/ap/3']],
    saved_wifi={('Home','wpa-psk'): 'wifi-uuid'})
cellular = dict(network, modems=[('/modem/0', {'generic': {'manufacturer':'Example', 'model':'LTE Modem',
    'state':'registered', 'unlock-required':'sim-pin', 'sim':'/sim/0', 'signal-quality':{'value':'85'},
    'unlock-retries':['sim-pin (3)'], 'device-identifier':'one'}, '3gpp': {'operator-name':'Carrier'}})])
adapter = '/org/bluez/hci0'
device = adapter + '/dev_00_11_22_33_44_55'
bluetooth = {adapter: {'org.bluez.Adapter1': {'Alias':'Bluetooth adapter', 'Powered':True, 'Address':'00:11:22:33:44:55'}},
    device: {'org.bluez.Device1': {'Adapter':adapter,'Alias':'Headphones','Address':'AA:BB:CC:DD:EE:FF','Paired':True,'Trusted':True,'Connected':False}}}
audio = [{'id':'42','kind':'Output','name':'Internal speakers','volume':53,'muted':False,'default':True},
         {'id':'43','kind':'Input','name':'Microphone','volume':70,'muted':True},
         {'id':'44','kind':'Output','name':'HDMI / DisplayPort','volume':50,'muted':False}]
power = dict(batteries=[dict(Model='Internal battery',Percentage=75.4,State=2,Capacity=97.0,TimeToEmpty=7200)],
    profiles=['power-saver','balanced','performance'],active='balanced',profile_error='',battery_error='')
profile = {'connection': {'autoconnect':True}, 'ipv4': {'method':'auto','dns-data':['1.1.1.1']}, 'ipv6':{'method':'auto'}}

def clock_load(self):
    self._zones = ['America/New_York','Europe/London','Etc/UTC']
    return dict(Timezone='America/New_York',NTP='yes',CanNTP='yes',NTPSynchronized='yes')

outputs = [dict(name='eDP-1', description='Built-in display', enabled=True, scale=1,
                position=dict(x=0,y=0), modes=[dict(width=1920,height=1080,refresh=60,current=True)])]
config = {'software_rendering_apps': [], 'shell': 'desktop'}
patches = [patch.object(display_page.displays, 'query_outputs', lambda: outputs),
    patch.object(display_page.displays, 'CONFIG_PATH', Path(temporary.name) / 'monitors.json'),
    patch.object(display_page.displays, 'STATUS_PATH', Path(temporary.name) / 'monitors-status.json'),
    patch.object(main, 'load_config', lambda: config),
    patch.object(main, 'set_shell', lambda value: config.update(shell=value)),
    patch.object(main, 'set_software_rendering_apps', lambda values: config.update(software_rendering_apps=values)),
    patch.object(main, 'wallpaper_enabled', lambda: True),
    patch.object(Gio, 'bus_get', lambda *args: None),
    patch.object(network_pages.WifiPage, 'load', lambda self: wifi),
    patch.object(network_pages.NetworkPage, 'load', lambda self: network),
    patch.object(network_pages.CellularPage, 'load', lambda self: cellular),
    patch.object(bluetooth_page.BluetoothPage, 'load', lambda self: bluetooth),
    patch.object(system_pages.AudioPage, 'load', lambda self: audio),
    patch.object(system_pages.PowerPage, 'load', lambda self: power),
    patch.object(system_pages.DateTimePage, 'load', clock_load),
    patch.object(connection_page.ConnectionPage, 'load', lambda self: profile),
    patch.object(bluetooth_page.BluetoothDevicePage, 'load', lambda self: bluetooth[device]['org.bluez.Device1'])]
for item in patches:
    item.start()

def settle(page=None):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if page is not None and not getattr(page, '_busy', False):
            break
        if page is None and time.monotonic() > deadline - 1.6:
            break
        time.sleep(.01)

def widgets(root):
    yield root
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            yield from widgets(child)


def row(root, title):
    return next(widget for widget in widgets(root) if isinstance(widget, Handy.ActionRow) and widget.get_title() == title)


def dialog(title):
    return next(widget for widget in Gtk.Window.list_toplevels() if isinstance(widget, Gtk.Dialog) and widget.get_title() == title)


def capture(widget, filename):
    directory = os.environ.get('YUI_SETTINGS_SCREENSHOT_DIR')
    if not directory:
        return
    settle()
    import cairo
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, widget.get_allocated_width() * 2, widget.get_allocated_height() * 2)
    surface.set_device_scale(2, 2)
    widget.draw(cairo.Context(surface))
    surface.write_to_png(str(destination / filename))


def navigate(name):
    selected = next(item for item in window._sidebar_list.get_children() if item.page_name == name)
    window._sidebar_list.select_row(selected)
    page = window._stack.get_visible_child()
    settle(page)
    return page


window = main.SettingsWindow(None)
assert window._shell_mode_row.get_active_id() == 'desktop'
window._shell_mode_row.set_selected_index(1)
assert config['shell'] == 'mobile'
window._shell_mode_row.set_selected_index(0)
assert config['shell'] == 'desktop'
with patch.object(main, 'set_shell', side_effect=OSError('Read-only config')):
    window._shell_mode_row.set_selected_index(1)
assert config['shell'] == 'desktop'
assert window._shell_mode_row.get_active_id() == 'desktop'
assert window._shell_mode_status.get_title() == 'Could not save Yui mode.'
window.show_all()
settle()
for name in ['wallpaper','appearance','display','wifi','cellular','network','bluetooth','audio','power','datetime','accessibility','advanced','about']:
    sidebar_row = next(item for item in window._sidebar_list.get_children() if item.page_name == name)
    window._sidebar_list.select_row(sidebar_row)
    window._stack.set_visible_child_name(name)
    page = window._stack.get_visible_child()
    settle(page)
    if hasattr(page, 'status'):
        assert not page.status.get_text(), (name, page.status.get_text())
        assert page.body.get_children(), name
        assert len(page.get_children()) == 1, 'Unexpected introductory widgets'
        assert not page.status.get_visible(), name
    if hasattr(page, 'refresh_button'):
        assert page.refresh_button.get_parent() == window._content_header, name
    settle()
    capture(window, name + '-desktop.png')
    print('Rendered', name, flush=True)

# Activating a saved network opens the actual configuration dialog.
page = navigate('wifi')
row(page, 'Home').activate()
editor_dialog = dialog('Home')
editor = next(item for item in widgets(editor_dialog) if isinstance(item, connection_page.ConnectionPage))
settle(editor)
assert not editor.status.get_text(), editor.status.get_text()
assert editor.refresh_button.get_parent() is None, 'Connection dialog exposes refresh'
assert row(editor, 'Address configuration')
settle()
capture(editor_dialog, 'connection-dialog.png')
with patch.object(connection_page.backend, 'save_network_preferences') as save:
    method, addresses, gateway, dns, ignore = editor._families[0]
    method.set_selected_index(1)
    addresses.set_text('192.168.1.10/24')
    gateway.set_text('192.168.1.1')
    editor.save_button.clicked()
    settle(editor)
    assert save.call_args.args[3][0]['addresses'] == '192.168.1.10/24'
editor_dialog.response(Gtk.ResponseType.CLOSE)
settle(page)
print('Saved network activation opens and saves connection options', flush=True)

# Inspect a real password dialog, then cancel it without touching a service.
def dismiss_password():
    prompt = dialog('Cafe: west\\east')
    entries = [item for item in widgets(prompt) if isinstance(item, Gtk.Entry)]
    assert len(entries) == 1 and not entries[0].get_visibility()
    capture(prompt, 'wifi-password-dialog.png')
    prompt.response(Gtk.ResponseType.CANCEL)
    return False
GLib.timeout_add(180, dismiss_password)
row(page, 'Cafe: west\\east').activate()
with patch.object(network_pages, 'form', return_value=['secret123']) as prompt, \
     patch.object(network_pages.backend, 'create_wifi', return_value='new-wifi') as create, \
     patch.object(network_pages.backend, 'activate_profile') as activate:
    row(page, 'Cafe: west\\east').activate()
    settle(page)
    assert len(prompt.call_args.args[2]) == 1
    create.assert_called_once_with('Cafe: west\\east', 'sae', 'secret123', hidden=False)
    activate.assert_called_once_with('new-wifi', 'wlan0')
with patch.object(network_pages, 'form') as prompt, \
     patch.object(network_pages.backend, 'create_wifi', return_value='guest-wifi') as create, \
     patch.object(network_pages.backend, 'activate_profile'):
    row(page, 'Guest').activate()
    settle(page)
    prompt.assert_not_called()
    create.assert_called_once_with('Guest', 'open', '', hidden=False)
print('Wi-Fi rows prompt for passwords and directly connect open networks', flush=True)

page = navigate('bluetooth')
row(page, 'Headphones').activate()
device_dialog = dialog('Headphones')
device_page = next(item for item in widgets(device_dialog) if isinstance(item, bluetooth_page.BluetoothDevicePage))
settle(device_page)
assert device_page.refresh_button.get_parent() is None, 'Device dialog exposes refresh'
assert row(device_page, 'Trusted accessory')
capture(device_dialog, 'bluetooth-device-dialog.png')
device_dialog.response(Gtk.ResponseType.CLOSE)
settle(page)

page = navigate('cellular')
row(page, 'Carrier').activate()
modem_dialog = dialog('Example LTE Modem')
assert row(modem_dialog, 'Unlock SIM…') and row(modem_dialog, 'Add APN connection…')
modem_dialog.response(Gtk.ResponseType.CLOSE)

page = navigate('display')
row(page, 'Built-in display').activate()
monitor_dialog = dialog('Built-in display')
assert row(monitor_dialog, 'Orientation')
capture(monitor_dialog, 'display-options-dialog.png')
custom_switch = row(monitor_dialog, 'Custom resolution').get_activatable_widget()
custom_switch.set_active(True)
settle()
custom_entry = next(item for item in widgets(monitor_dialog) if isinstance(item, Gtk.Entry)
                    and item.get_placeholder_text() == '1920x1080@60Hz')
assert custom_entry.get_mapped() and custom_entry.get_editable()
custom_entry.set_text('')
monitor_dialog.response(Gtk.ResponseType.OK)
assert monitor_dialog.get_visible(), 'Empty custom mode was accepted'
custom_entry.set_text('1600x900@75Hz')
scale = next(item for item in widgets(monitor_dialog) if isinstance(item, Gtk.SpinButton))
scale.set_value(1.25)
monitor_dialog.response(Gtk.ResponseType.OK)
assert page._dirty and page._save.get_sensitive()
assert page._profiles[0]['mode'] == '1600x900@75Hz' and page._profiles[0]['custom_mode']
assert '--custom-mode' in display_page.displays.build_arguments(page._profiles, outputs)
assert not display_page.displays.CONFIG_PATH.exists(), 'Display edit applied before confirmation'
page._save.clicked()
assert display_page.displays.CONFIG_PATH.exists()
assert not page._dirty
assert display_page.displays.load_profiles()[0]['mode'] == '1600x900@75Hz'
print('Custom resolution accepts unlisted modes and stages edits until Apply', flush=True)

page = navigate('advanced')
assert window._show_window_buttons.get_parent().is_ancestor(page)
assert window._undo_window_buttons.get_label() == 'Undo fix'
window._software_app_entry.set_text('org.example.App.desktop')
window._add_software_app(None)
assert config['software_rendering_apps'] == ['org.example.App.desktop']
assert window._software_app_entry.get_text() == ''
remove = next(item for item in widgets(page) if isinstance(item, Gtk.Button) and item.get_tooltip_text() == 'Remove org.example.App.desktop')
remove.clicked()
assert config['software_rendering_apps'] == []
print('Advanced retains its original inline controls and application list', flush=True)

page = navigate('audio')
with patch.object(system_pages.backend, 'run') as run:
    selector = next(item for item in widgets(page) if isinstance(item, service_page.ChoiceRow))
    selector.set_selected_index(1)
    settle(page)
    run.assert_called_once_with('wpctl', 'set-default', '44')
with patch.object(system_pages.backend, 'run') as run:
    slider = next(item for item in widgets(page) if isinstance(item, system_pages.VolumeSlider))
    slider.set_value(40)
    slider.set_value(65)
    slider.set_value(80)
    settle()
    run.assert_called_once_with('wpctl', 'set-volume', '42', '80%', '--limit', '1.0')
print('Audio selection and volume apply directly with coalesced slider writes', flush=True)

class Invocation:
    def __init__(self): self.value = None; self.error = None; self.done = False
    def return_value(self, value): self.value = value; self.done = True
    def return_dbus_error(self, name, message): self.error = name; self.done = True

bt = window._stack.get_child_by_name('bluetooth')
bt._pairing = device
bt._bluez_owner = ':1.100'
invocation = Invocation()
bt._agent_call(None, ':1.100', None, None, 'RequestConfirmation', GLib.Variant('(ou)', (device, 123456)), invocation)
assert bt._dialog is not None
bt._dialog.response(Gtk.ResponseType.OK)
assert invocation.done and not invocation.error
invocation = Invocation()
bt._agent_call(None, ':1.200', None, None, 'RequestConfirmation', GLib.Variant('(ou)', (device, 123456)), invocation)
assert invocation.error == 'org.bluez.Error.Rejected'
bt._pairing = None
print('Pairing agent confirms codes and rejects other senders', flush=True)

page = window._stack.get_child_by_name('wifi')
window._stack.set_visible_child_name('wifi')
settle(page)
def fail(): raise RuntimeError('Permission denied (test)')
page.perform(fail)
settle(page)
assert 'Permission denied' in page.status.get_text()
assert page.body.get_sensitive() and page.refresh_button.get_sensitive()
print('Failed operation restores controls and reports error', flush=True)
page.reload()
settle(page)

window._leaflet.set_visible_child(window._content_pane)
window.resize(360, 640)
settle()
window._sidebar_list.select_row(window._sidebar_list.get_row_at_index(0))
window._stack.set_visible_child_name('wifi')
settle(page)
width, height = window.get_size()
print('Phone window size', width, height, 'folded', window._leaflet.get_folded(), flush=True)
assert window._leaflet.get_folded()
assert width <= 400, width
settle()
assert window._content_header.get_title() == 'Wi-Fi'
for name in ['wifi', 'network', 'audio', 'display', 'accessibility']:
    page = navigate(name)
    settle()
    capture(window, name + '-phone.png')
page = navigate('wifi')
settle()
for name in ['wifi', 'cellular', 'network', 'bluetooth', 'audio', 'power', 'datetime', 'accessibility', 'display', 'advanced', 'about']:
    page = navigate(name)
    window.resize(320, 640)
    settle()
    assert window.get_size()[0] <= 320, (name, window.get_size())
page = navigate('display')
row(page, 'Built-in display').activate()
monitor_dialog = dialog('Built-in display')
settle()
assert monitor_dialog.get_size()[0] <= 320, monitor_dialog.get_size()
custom_entry = next(item for item in widgets(monitor_dialog) if isinstance(item, Gtk.Entry)
                    and item.get_placeholder_text() == '1920x1080@60Hz')
assert custom_entry.get_mapped() and custom_entry.get_text() == '1600x900@75Hz'
row(monitor_dialog, 'Custom resolution').get_activatable_widget().set_active(False)
presets = next(item for item in widgets(monitor_dialog) if isinstance(item, Gtk.ComboBoxText))
presets.set_active_id('1920x1080@60Hz')
monitor_dialog.response(Gtk.ResponseType.OK)
assert not page._profiles[0]['custom_mode']
assert page._profiles[0]['mode'] == '1920x1080@60Hz'
assert '--mode' in display_page.displays.build_arguments(page._profiles, outputs)
settle()
assert page._save.get_visible() and window.get_size()[0] <= 320
page._save.clicked()
page = navigate('wifi')
row(page, 'Home').activate()
phone_dialog = dialog('Home')
phone_editor = next(item for item in widgets(phone_dialog) if isinstance(item, connection_page.ConnectionPage))
settle(phone_editor)
assert phone_editor.refresh_button.get_parent() is None, 'Phone dialog exposes refresh'
settle()
assert phone_dialog.get_size()[0] <= 320, phone_dialog.get_size()
capture(phone_dialog, 'connection-dialog-phone.png')
phone_dialog.response(Gtk.ResponseType.CLOSE)
settle(page)
print('All pages and connection dialog fit a 320-pixel phone window', flush=True)
if os.environ.get('YUI_SETTINGS_SCREENSHOT'):
    settle()
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, window.get_allocated_width() * 2, window.get_allocated_height() * 2)
    surface.set_device_scale(2, 2)
    window.draw(cairo.Context(surface))
    surface.write_to_png(os.environ['YUI_SETTINGS_SCREENSHOT'])
    print('Saved phone screenshot', flush=True)
window.destroy()
settle()
temporary.cleanup()
print('GTK smoke checks passed', flush=True)
