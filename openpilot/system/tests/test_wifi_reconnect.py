from openpilot.system.ui.lib.networkmanager import NM, NM_IFACE, NM_PATH, NMDeviceState
import time
from types import SimpleNamespace
from jeepney import DBusAddress
from unittest.mock import MagicMock

from openpilot.system.wifi_reconnect import RECONNECT_TIMEOUT, NetworkManagerWifi, reconnect_last_wifi, reconnect_with_deadline


class FakeClock:
  def __init__(self):
    self.now = 0.0

  def monotonic(self) -> float:
    return self.now

  def sleep(self, seconds: float) -> None:
    self.now += seconds


class FakeConnector:
  def __init__(self, states: list[NMDeviceState]):
    self.states = states
    self.index = 0
    self.attempts: list[str] = []

  def state(self) -> NMDeviceState:
    state = self.states[min(self.index, len(self.states) - 1)]
    self.index += 1
    return state

  def activate_saved(self, ssid: str) -> bool:
    self.attempts.append(ssid)
    return True


def run_reconnect(connector: FakeConnector, clock: FakeClock, **kwargs) -> bool:
  return reconnect_last_wifi('Desk WiFi', connector_factory=lambda: connector,
                             monotonic=clock.monotonic, sleep=clock.sleep, **kwargs)


def test_reconnect_stops_when_connected():
  clock = FakeClock()
  connector = FakeConnector([NMDeviceState.DISCONNECTED, NMDeviceState.PREPARE, NMDeviceState.ACTIVATED])

  assert run_reconnect(connector, clock, timeout=60.0)
  assert connector.attempts == ['Desk WiFi']
  assert clock.now == 2.0


def test_reconnect_retries_until_timeout():
  clock = FakeClock()
  connector = FakeConnector([NMDeviceState.DISCONNECTED])

  assert not run_reconnect(connector, clock, timeout=5.0, retry_interval=2.0)
  assert connector.attempts == ['Desk WiFi', 'Desk WiFi', 'Desk WiFi']
  assert clock.now == 5.0


def test_reconnect_does_not_interrupt_connection_in_progress():
  clock = FakeClock()
  connector = FakeConnector([NMDeviceState.CONFIG])

  assert not run_reconnect(connector, clock, timeout=3.0)
  assert connector.attempts == []


def test_reconnect_recovers_when_networkmanager_starts_late():
  clock = FakeClock()
  connector = FakeConnector([NMDeviceState.DISCONNECTED, NMDeviceState.DISCONNECTED, NMDeviceState.ACTIVATED])
  calls = 0

  def factory():
    nonlocal calls
    calls += 1
    if calls == 1:
      raise FileNotFoundError
    return connector

  assert reconnect_last_wifi('Desk WiFi', timeout=10.0, retry_interval=2.0,
                             connector_factory=factory, monotonic=clock.monotonic, sleep=clock.sleep)
  assert calls == 2
  assert connector.attempts == ['Desk WiFi']


def test_default_reconnect_window_is_twenty_seconds():
  assert RECONNECT_TIMEOUT == 20.0


def test_saved_connection_skips_access_point_and_matches_ssid():
  connector = NetworkManagerWifi.__new__(NetworkManagerWifi)
  connector._call = MagicMock(side_effect=[
    SimpleNamespace(body=[['/ap', '/phone']]),
    SimpleNamespace(body=[{'802-11-wireless': {'mode': ('s', 'ap'), 'ssid': ('ay', b'Desk WiFi')}}]),
    SimpleNamespace(body=[{'802-11-wireless': {'mode': ('s', 'infrastructure'), 'ssid': ('ay', b'Desk WiFi')}}]),
  ])

  assert connector._saved_connection('Desk WiFi') == '/phone'
  assert connector._call.call_count == 3


def test_activate_saved_uses_matching_profile_and_wifi_device():
  connector = NetworkManagerWifi.__new__(NetworkManagerWifi)
  connector._wifi_device = MagicMock(return_value='/wifi-device')
  connector._saved_connection = MagicMock(return_value='/phone-profile')
  connector._call = MagicMock(return_value=SimpleNamespace(body=[]))
  connector._nm = DBusAddress(NM_PATH, bus_name=NM, interface=NM_IFACE)

  assert connector.activate_saved('Desk WiFi')
  assert connector._call.call_args.args[0].body == ('/phone-profile', '/wifi-device', '/')


def test_deadline_interrupts_stalled_dbus_setup():
  def stalled_factory():
    time.sleep(0.2)
    raise AssertionError("deadline did not interrupt setup")

  start = time.monotonic()
  assert not reconnect_with_deadline('Desk WiFi', timeout=0.02, connector_factory=stalled_factory)
  assert time.monotonic() - start < 0.15
