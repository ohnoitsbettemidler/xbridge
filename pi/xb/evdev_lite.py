"""Minimal reader for Linux input devices (/dev/input/event*).

Does the small part of python-evdev that XBridge needs, so nothing has to be
installed on the Pi: find keyboards and mice, take them over (so key presses
don't also go to the Pi's own console), read their events, and set the
keyboard's Caps/Num/Scroll Lock lights.
"""

import errno
import fcntl
import glob
import os
import struct

EV_SYN, EV_KEY, EV_REL, EV_LED = 0x00, 0x01, 0x02, 0x11
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
LED_NUML, LED_CAPSL, LED_SCROLLL = 0x00, 0x01, 0x02
KEY_A, KEY_Z, KEY_SPACE, BTN_LEFT = 30, 44, 57, 0x110

# struct input_event: timeval (two longs, native size) + type, code, value
EVENT_FMT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FMT)

_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord('E') << 8) | nr


EVIOCGRAB = _ioc(_IOC_WRITE, 0x90, 4)


def EVIOCGNAME(length):
    return _ioc(_IOC_READ, 0x06, length)


def EVIOCGBIT(ev_type, length):
    return _ioc(_IOC_READ, 0x20 + ev_type, length)


def _bits(fd, ev_type, nbytes):
    buf = bytearray(nbytes)
    try:
        fcntl.ioctl(fd, EVIOCGBIT(ev_type, nbytes), buf, True)
    except OSError:
        return bytearray(nbytes)
    return buf


def _has(bits, code):
    return bool(bits[code // 8] & (1 << (code % 8))) if code // 8 < len(bits) else False


def parse_events(data):
    """Split raw bytes into (type, code, value) tuples."""
    out = []
    for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
        _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, i)
        out.append((etype, code, value))
    return out


class InputDevice:
    def __init__(self, path):
        self.path = path
        self.writable = True
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            self.writable = False
        buf = bytearray(256)
        try:
            n = fcntl.ioctl(self.fd, EVIOCGNAME(256), buf, True)
            self.name = bytes(buf[:max(0, n - 1)]).decode('utf-8', 'replace') or path
        except OSError:
            self.name = path
        keys = _bits(self.fd, EV_KEY, 96)
        rels = _bits(self.fd, EV_REL, 2)
        leds = _bits(self.fd, EV_LED, 2)
        self.is_keyboard = all(_has(keys, c) for c in range(KEY_A, KEY_Z + 1)) or \
            (_has(keys, KEY_A) and _has(keys, KEY_SPACE))
        self.is_mouse = _has(rels, REL_X) and _has(rels, REL_Y) and _has(keys, BTN_LEFT)
        self.has_leds = any(_has(leds, c) for c in (LED_NUML, LED_CAPSL, LED_SCROLLL))
        self.pressed = set()     # key names currently held on this device
        self.grabbed = False

    @property
    def kind(self):
        if self.is_keyboard and self.is_mouse:
            return 'keyboard + mouse'
        return 'keyboard' if self.is_keyboard else ('mouse' if self.is_mouse else 'other')

    def grab(self):
        fcntl.ioctl(self.fd, EVIOCGRAB, 1)
        self.grabbed = True

    def read(self):
        """Return pending events. Raises OSError if the device has gone."""
        events = []
        while True:
            try:
                data = os.read(self.fd, EVENT_SIZE * 64)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno == errno.EAGAIN:
                    break
                raise
            if not data:
                raise OSError(errno.ENODEV, 'device closed')
            events.extend(parse_events(data))
            if len(data) < EVENT_SIZE * 64:
                break
        return events

    def set_led(self, led, on):
        if not (self.writable and self.has_leds):
            return
        try:
            os.write(self.fd, struct.pack(EVENT_FMT, 0, 0, EV_LED, led, 1 if on else 0) +
                     struct.pack(EVENT_FMT, 0, 0, EV_SYN, 0, 0))
        except OSError:
            pass

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


class InputManager:
    """Keeps track of keyboards and mice, including ones plugged in later."""

    def __init__(self, grab=True, pattern='/dev/input/event*', log=print):
        self.grab = grab
        self.pattern = pattern
        self.log = log
        self.devices = {}     # fd -> InputDevice
        self.ignored = set()  # paths that aren't keyboards/mice

    def scan(self):
        present = set(glob.glob(self.pattern))
        self.ignored &= present
        known = {d.path for d in self.devices.values()}
        for path in sorted(present - known - self.ignored):
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            if not (dev.is_keyboard or dev.is_mouse):
                dev.close()
                self.ignored.add(path)
                continue
            if self.grab:
                try:
                    dev.grab()
                except OSError as exc:
                    self.log('Could not take over %s (%s); using it shared' % (dev.name, exc))
            self.devices[dev.fd] = dev
            self.log('Connected: %s (%s)' % (dev.name, dev.kind))

    def fds(self):
        return list(self.devices.keys())

    def remove(self, fd):
        dev = self.devices.pop(fd, None)
        if dev:
            self.log('Disconnected: %s' % dev.name)
            dev.close()
        return dev

    def set_leds(self, scroll, num, caps):
        for dev in self.devices.values():
            if dev.is_keyboard:
                dev.set_led(LED_SCROLLL, scroll)
                dev.set_led(LED_NUML, num)
                dev.set_led(LED_CAPSL, caps)

    def summary(self):
        return [{'name': d.name, 'kind': d.kind, 'grabbed': d.grabbed} for d in self.devices.values()]

    def close(self):
        for fd in list(self.devices):
            self.remove(fd)
