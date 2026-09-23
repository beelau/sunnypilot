from openpilot.system.ui.lib.networkmanager import NMDeviceState
from openpilot.system.wifi_reconnect import reconnect_last_wifi


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
