"""Quick-control snapshots and icon selection, independent of GTK."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import re

# Share the Settings adapters without importing its GTK pages or changing sys.path.
_backend_path = (Path(__file__).resolve().parents[1] /
                 'Applications/Production/Yui/YuiSettings/system_backend.py')
_spec = importlib.util.spec_from_file_location('yui_service_backend', _backend_path)
services = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(services)


def network_state():
    rows = services.nm_rows('DEVICE,TYPE,STATE,CONNECTION', 'device', 'status')
    radio = services.nm('radio', 'wifi') == 'enabled'
    wireless = any(row[1] == 'wifi' for row in rows)
    connected = [row for row in rows if row[2] == 'connected' and row[1] != 'loopback']
    wired = next((row for row in connected if row[1] == 'ethernet'), None)
    wifi = next((row for row in connected if row[1] == 'wifi'), None)
    if wired:
        return dict(icon='network-wired-symbolic', text=f'Connected: {wired[3]}', wifi=radio, wireless=wireless)
    if wifi:
        access_points = services.nm_rows('IN-USE,SIGNAL', 'device', 'wifi', 'list',
                                       'ifname', wifi[0], '--rescan', 'no')
        signal = next((int(row[1]) for row in access_points if row[0] == '*'), 0)
        level = 'excellent' if signal >= 80 else 'good' if signal >= 55 else 'ok' if signal >= 30 else 'weak'
        return dict(icon=f'network-wireless-signal-{level}-symbolic', text=f'{wifi[3]} · {signal}%', wifi=radio, wireless=wireless)
    if connected:
        return dict(icon='network-transmit-receive-symbolic', text=f'Connected: {connected[0][3]}', wifi=radio, wireless=wireless)
    return dict(icon='network-offline-symbolic', text='Disconnected' if radio else 'Wi-Fi off', wifi=radio, wireless=wireless)


def audio_state():
    output = services.run('wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@', timeout=5)
    match = re.search(r'Volume:\s+([0-9.]+)', output)
    if not match:
        raise ValueError('Could not read the default output volume.')
    volume = round(float(match[1]) * 100)
    muted = '[MUTED]' in output
    level = 'muted' if muted or volume == 0 else 'low' if volume < 34 else 'medium' if volume < 67 else 'high'
    return dict(icon=f'audio-volume-{level}-symbolic', text=f'{"Muted · " if muted else ""}{volume}%', volume=volume, muted=muted)


def battery_state():
    # UPower's display device aggregates system batteries and excludes peripherals.
    props = services.dbus_call('org.freedesktop.UPower', '/org/freedesktop/UPower/devices/DisplayDevice',
                               'org.freedesktop.DBus.Properties', 'GetAll', '(s)',
                               ('org.freedesktop.UPower.Device',))[0]
    if not props.get('IsPresent'):
        return dict(icon='battery-missing-symbolic', text='No system battery')
    percent = max(0, min(100, round(props.get('Percentage', 0))))
    state = props.get('State', 0)
    level = 'empty' if percent < 10 else 'caution' if percent < 20 else 'low' if percent < 40 else 'good' if percent < 80 else 'full'
    charging = state in (1, 5)
    suffix = '-charging' if charging else ''
    icon = 'battery-full-charged-symbolic' if state == 4 else f'battery-{level}{suffix}-symbolic'
    description = 'Charging' if charging else 'Fully charged' if state == 4 else 'Discharging' if state == 2 else 'Battery'
    return dict(icon=icon, text=f'{description} · {percent}%')


def bluetooth_state():
    objects = services.dbus_call('org.bluez', '/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects')[0]
    adapters = [(path, item['org.bluez.Adapter1']) for path, item in objects.items() if 'org.bluez.Adapter1' in item]
    powered = any(props.get('Powered') for _, props in adapters)
    connected = sum(bool(item.get('org.bluez.Device1', {}).get('Connected')) for item in objects.values())
    return dict(icon='bluetooth-active-symbolic' if powered else 'bluetooth-disabled-symbolic',
                text=f'Bluetooth · {connected} connected' if powered else 'Bluetooth off',
                powered=powered, adapters=[path for path, _ in adapters])


def snapshot():
    readers = {'network': network_state, 'audio': audio_state, 'pwr': battery_state,
               'bt': bluetooth_state, 'brightness': lambda: dict(devices=services.backlights())}
    fallbacks = {'network': 'network-offline-symbolic', 'audio': 'audio-volume-muted-symbolic',
                 'pwr': 'battery-missing-symbolic', 'bt': 'bluetooth-disabled-symbolic'}
    def read(item):
        name, reader = item
        try:
            return name, reader()
        except Exception as exc:
            return name, dict(icon=fallbacks.get(name, ''), text=f'Unavailable: {exc}', error=str(exc))
    with ThreadPoolExecutor(max_workers=len(readers)) as pool:
        return dict(pool.map(read, readers.items()))


def set_volume(percent):
    percent = max(0, min(100, int(percent)))
    services.run('wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{percent}%', timeout=5)


def set_muted(muted):
    services.run('wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1' if muted else '0', timeout=5)


def set_wifi(enabled):
    services.nm('radio', 'wifi', 'on' if enabled else 'off')


def set_bluetooth(paths, enabled):
    from gi.repository import GLib
    for path in paths:
        services.dbus_call('org.bluez', path, 'org.freedesktop.DBus.Properties', 'Set',
                          '(ssv)', ('org.bluez.Adapter1', 'Powered', GLib.Variant('b', enabled)))
