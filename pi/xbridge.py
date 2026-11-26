#!/usr/bin/env python3
"""XBridge: mouse and keyboard on the Pi -> Xbox controller via the Feather.

Runs as a service (see install.sh). Useful options when testing by hand:
  --no-grab   leave keyboards/mice working on the Pi as normal
  --dry-run   don't talk to the Feather; just run the settings page
"""

import argparse
import copy
import os
import select
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xb import config as cfgmod                      # noqa: E402
from xb.evdev_lite import (EV_KEY, EV_REL, REL_WHEEL, REL_X, REL_Y,  # noqa: E402
                           InputManager)
from xb.keycodes import CODE_NAMES                  # noqa: E402
from xb.link import I2CLink                         # noqa: E402
from xb.mapper import ControllerState, Mapper       # noqa: E402
from xb.web import Shared, WebServer                # noqa: E402

DEFAULT_CONFIG = '/etc/xbridge/config.json'
SCAN_EVERY_S = 2.0
STATUS_EVERY_S = 0.2


def log(msg):
    print(time.strftime('%H:%M:%S ') + msg, flush=True)


class NullLink:
    """Stands in for the Feather with --dry-run."""
    ok = False
    sent = errors = 0
    last_error = 'dry run - not talking to the Feather'
    feather = None

    def send(self, state):
        return False

    def close(self):
        pass


class Bridge:
    def __init__(self, args):
        self.args = args
        cfg, problem = cfgmod.load(args.config)
        if problem:
            log(problem)
        self.config_note = problem
        self.shared = Shared(cfg, args.config)
        self.mapper = Mapper(copy.deepcopy(cfg))
        self.inputs = InputManager(grab=not args.no_grab, log=log)
        self.link = self._make_link(cfg)
        self.period = 1.0 / cfg['rate_hz']
        self.leds = None
        self.running = True
        self.started = time.monotonic()
        self.ticks = 0
        self.rate_window = (self.started, 0)
        self.measured_rate = 0.0
        self.last_state = ControllerState()

    def _make_link(self, cfg):
        if self.args.dry_run:
            return NullLink()
        return I2CLink(cfg['i2c_bus'], cfg['i2c_address'])

    # ------------------------------------------------------------- events
    def handle_device(self, fd):
        dev = self.inputs.devices.get(fd)
        if dev is None:
            return
        try:
            events = dev.read()
        except OSError:
            self.mapper.release_keys(dev.pressed)
            self.inputs.remove(fd)
            return
        now = time.monotonic()
        dx = dy = 0
        for etype, code, value in events:
            if etype == EV_KEY:
                name = CODE_NAMES.get(code)
                if name is None or value == 2:   # unknown key, or auto-repeat
                    continue
                if value:
                    dev.pressed.add(name)
                else:
                    dev.pressed.discard(name)
                self.mapper.key(name, bool(value), now)
            elif etype == EV_REL:
                if code == REL_X:
                    dx += value
                elif code == REL_Y:
                    dy += value
                elif code == REL_WHEEL and value:
                    self.mapper.scroll(value, now)
        if dx or dy:
            self.mapper.mouse_move(dx, dy, now)

    # --------------------------------------------------------------- tick
    def apply_pending_config(self):
        cfg = self.shared.take_pending()
        if cfg is None:
            return
        old = self.mapper.cfg
        self.mapper.set_config(copy.deepcopy(cfg))
        self.period = 1.0 / cfg['rate_hz']
        if (cfg['i2c_bus'], cfg['i2c_address']) != (old['i2c_bus'], old['i2c_address']):
            self.link.close()
            self.link = self._make_link(cfg)
        log('Settings updated')

    def tick(self, now):
        self.apply_pending_config()
        state = self.mapper.tick(now)
        self.last_state = state
        self.link.send(state)

        if self.mapper.config_changed:
            self.mapper.config_changed = False
            try:
                self.shared.set_config_from_loop(copy.deepcopy(self.mapper.cfg))
            except OSError as exc:
                log('Could not save settings: %s' % exc)

        if self.mapper.cfg.get('keyboard_leds', True):
            leds = self.mapper.leds()
            if leds != self.leds:
                self.leds = leds
                self.inputs.set_leds(**leds)

        self.ticks += 1
        t0, n0 = self.rate_window
        if now - t0 >= 1.0:
            self.measured_rate = (self.ticks - n0) / (now - t0)
            self.rate_window = (now, self.ticks)

    def publish_status(self, now):
        m = self.mapper.status()
        link = self.link
        self.shared.status = {
            'uptime_s': round(now - self.started),
            'hostname': socket.gethostname(),
            'profile': m['profile'],
            'paused': m['paused'],
            'aiming': m['aiming'],
            'wheel_running': m['wheel_running'],
            'finder_running': m['finder_running'],
            'messages': m['messages'],
            'devices': self.inputs.summary(),
            'link': {
                'ok': link.ok,
                'sent': link.sent,
                'errors': link.errors,
                'last_error': link.last_error,
                'feather': link.feather,
            },
            'output': self.last_state.as_dict(),
            'rate_hz': round(self.measured_rate),
            'dry_run': bool(self.args.dry_run),
            'config_note': self.config_note,
            'config_version': self.shared.version,
        }

    # --------------------------------------------------------------- loop
    def run(self):
        web = WebServer(self.shared, port=self.args.port)
        web.start()
        log('Settings page on port %d' % web.port)

        next_tick = time.monotonic()
        next_scan = 0.0
        next_status = 0.0
        while self.running:
            now = time.monotonic()
            if now >= next_scan:
                self.inputs.scan()
                next_scan = now + SCAN_EVERY_S
            timeout = max(0.0, next_tick - now)
            fds = self.inputs.fds()
            try:
                readable = select.select(fds, [], [], timeout)[0] if fds else []
                if not fds:
                    time.sleep(timeout)
            except (OSError, ValueError):
                readable = []
                self.inputs.scan()
            for fd in readable:
                self.handle_device(fd)

            now = time.monotonic()
            if now >= next_tick:
                self.tick(now)
                next_tick += self.period
                if next_tick < now:          # fell behind: don't try to catch up
                    next_tick = now + self.period
            if now >= next_status:
                self.publish_status(now)
                next_status = now + STATUS_EVERY_S

        # Shutting down: let go of everything on the Xbox side first.
        self.link.send(ControllerState())
        self.link.close()
        self.inputs.set_leds(scroll=False, num=False, caps=False)
        self.inputs.close()
        web.stop()

    def stop(self, *_):
        self.running = False


def main(argv=None):
    p = argparse.ArgumentParser(description='XBridge mouse/keyboard to Xbox bridge')
    p.add_argument('--config', default=DEFAULT_CONFIG)
    p.add_argument('--port', type=int, default=8080)
    p.add_argument('--no-grab', action='store_true', help="don't take over keyboards/mice")
    p.add_argument('--dry-run', action='store_true', help="don't talk to the Feather")
    args = p.parse_args(argv)

    bridge = Bridge(args)
    signal.signal(signal.SIGTERM, bridge.stop)
    signal.signal(signal.SIGINT, bridge.stop)
    log('XBridge starting')
    bridge.run()
    log('XBridge stopped')


if __name__ == '__main__':
    main()
