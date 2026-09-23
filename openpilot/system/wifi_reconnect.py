#!/usr/bin/env python3
import time
from collections.abc import Callable

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import DBusConnection, open_dbus_connection
from jeepney.low_level import MessageType
from jeepney.wrappers import Properties

from openpilot.system.ui.lib.networkmanager import (NM, NM_CONNECTION_IFACE, NM_DEVICE_IFACE, NM_DEVICE_TYPE_WIFI,
                                                    NM_IFACE, NM_PATH, NM_SETTINGS_IFACE, NM_SETTINGS_PATH,
                                                    NMDeviceState)

RECONNECT_TIMEOUT = 60.0
RETRY_INTERVAL = 5.0
POLL_INTERVAL = 1.0

CONNECTING_STATES = {
  NMDeviceState.PREPARE,
  NMDeviceState.CONFIG,
  NMDeviceState.NEED_AUTH,
  NMDeviceState.IP_CONFIG,
  NMDeviceState.IP_CHECK,
  NMDeviceState.SECONDARIES,
}


class NetworkManagerWifi:
  """Small NetworkManager client used only during the boot reconnect window."""

  def __init__(self):
    self._conn: DBusConnection = open_dbus_connection(bus="SYSTEM")
    self._nm = DBusAddress(NM_PATH, bus_name=NM, interface=NM_IFACE)

  def _call(self, message):
    reply = self._conn.send_and_get_reply(message)
    if reply.header.message_type == MessageType.error:
      raise RuntimeError(str(reply.body))
    return reply

  def _wifi_device(self) -> str | None:
    reply = self._call(new_method_call(self._nm, 'GetDevices'))
    for device_path in reply.body[0]:
      device_addr = DBusAddress(device_path, bus_name=NM, interface=NM_DEVICE_IFACE)
      device_type = self._call(Properties(device_addr).get('DeviceType')).body[0][1]
      if device_type == NM_DEVICE_TYPE_WIFI:
        return str(device_path)
    return None

  def state(self) -> NMDeviceState:
    device_path = self._wifi_device()
    if device_path is None:
      return NMDeviceState.UNKNOWN

    device_addr = DBusAddress(device_path, bus_name=NM, interface=NM_DEVICE_IFACE)
    value = self._call(Properties(device_addr).get('State')).body[0][1]
    return NMDeviceState(value)

  def _saved_connection(self, ssid: str) -> str | None:
    settings_addr = DBusAddress(NM_SETTINGS_PATH, bus_name=NM, interface=NM_SETTINGS_IFACE)
    connection_paths = self._call(new_method_call(settings_addr, 'ListConnections')).body[0]

    for connection_path in connection_paths:
      connection_addr = DBusAddress(connection_path, bus_name=NM, interface=NM_CONNECTION_IFACE)
      settings = dict(self._call(new_method_call(connection_addr, 'GetSettings')).body[0])
      wireless = settings.get('802-11-wireless')
      if wireless is None or wireless.get('mode', ('s', 'infrastructure'))[1] == 'ap':
        continue

      saved_ssid = bytes(wireless.get('ssid', ('ay', b''))[1]).decode('utf-8', 'replace')
      if saved_ssid == ssid:
        return str(connection_path)
    return None

  def activate_saved(self, ssid: str) -> bool:
    device_path = self._wifi_device()
    connection_path = self._saved_connection(ssid)
    if device_path is None or connection_path is None:
      return False

    self._call(new_method_call(self._nm, 'ActivateConnection', 'ooo',
                               (connection_path, device_path, '/')))
    return True


def reconnect_last_wifi(ssid: str, *, timeout: float = RECONNECT_TIMEOUT,
                        retry_interval: float = RETRY_INTERVAL, poll_interval: float = POLL_INTERVAL,
                        connector_factory: Callable[[], NetworkManagerWifi] = NetworkManagerWifi,
                        monotonic: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep,
                        log_exception: Callable[[str], None] | None = None) -> bool:
  deadline = monotonic() + timeout
  next_attempt = 0.0
  connector: NetworkManagerWifi | None = None

  while monotonic() < deadline:
    now = monotonic()
    try:
      if connector is None:
        connector = connector_factory()

      state = connector.state()
      if state == NMDeviceState.ACTIVATED:
        return True

      if state not in CONNECTING_STATES and now >= next_attempt:
        connector.activate_saved(ssid)
        next_attempt = now + retry_interval
    except Exception:
      # NetworkManager and the Wi-Fi device can appear after manager starts. Reopen
      # D-Bus on the next poll and keep trying for the rest of the boot window.
      if log_exception is not None:
        log_exception("Failed to reconnect to saved Wi-Fi network")
      connector = None
      next_attempt = now + retry_interval

    sleep(min(poll_interval, max(0.0, deadline - monotonic())))

  return False


def main() -> None:
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog

  params = Params()
  try:
    ssid = params.get('LastWifiSSID')
    if not ssid:
      cloudlog.info('No last Wi-Fi network saved; skipping boot reconnect')
      return

    connected = reconnect_last_wifi(ssid, log_exception=cloudlog.exception)
    cloudlog.info(f"Boot Wi-Fi reconnect {'succeeded' if connected else 'timed out'}")
  finally:
    params.put_bool('WifiReconnectDone', True, block=True)


if __name__ == '__main__':
  main()
