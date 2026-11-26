"""The wire between the Pi and the Feather.

The Pi is the I2C controller; the Feather sits at address 0x42 on a
software I2C bus (GPIO23 = SDA, GPIO24 = SCL, set up by the installer).

Every packet is 14 bytes - see firmware/pilink.h for the same layout:
  seq, buttons(2), LX(2), LY(2), RX(2), RY(2), LT, RT, CRC-8
Sticks use 0..65535 with 0 = left / up. Triggers 0..255.
"""

import fcntl
import os
import struct
import time

I2C_SLAVE = 0x0703          # ioctl from linux/i2c-dev.h
PACKET_SIZE = 14
STATUS_SIZE = 4
CRC_INIT = 0x5A
STATUS_MAGIC = 0xB7

# Button bit positions - must match PILINK_BTN_* in the firmware.
BUTTON_BITS = {
    'A': 0, 'B': 1, 'X': 2, 'Y': 3,
    'LB': 4, 'RB': 5, 'VIEW': 6, 'MENU': 7,
    'LS': 8, 'RS': 9, 'GUIDE': 10, 'SHARE': 11,
    'DUP': 12, 'DDOWN': 13, 'DLEFT': 14, 'DRIGHT': 15,
}


def crc8(data, init=CRC_INIT):
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _axis(value):
    """-1..1 to 0..65535 (0 = negative end)."""
    v = max(-1.0, min(1.0, float(value)))
    return int(round((v + 1.0) * 32767.5))


def encode(state, seq):
    """Build the 14-byte packet for a ControllerState."""
    buttons = 0
    for name in state.buttons:
        bit = BUTTON_BITS.get(name)
        if bit is not None:
            buttons |= 1 << bit
    lx = _axis(state.lx)
    ly = _axis(-state.ly)      # our ly is +1 = up; the wire uses 0 = up
    rx = _axis(state.rx)
    ry = _axis(-state.ry)
    lt = max(0, min(255, int(round(state.lt * 255))))
    rt = max(0, min(255, int(round(state.rt * 255))))
    body = struct.pack('<BHHHHHBB', seq & 0xFF, buttons, lx, ly, rx, ry, lt, rt)
    return body + bytes([crc8(body)])


def decode_status(raw):
    """Turn the Feather's 4 status bytes into a dict (None if garbage)."""
    if len(raw) != STATUS_SIZE or raw[0] != STATUS_MAGIC:
        return None
    flags = raw[2]
    return {
        'version': raw[1],
        'xbox_connected': bool(flags & 0x01),
        'xbox_authenticated': bool(flags & 0x02),
        'xbox_mode': bool(flags & 0x04),
        'receiving': bool(flags & 0x08),
        'bad_packets': raw[3],
    }


class I2CLink:
    """Sends packets to the Feather. Never raises during play: if the Feather
    is unplugged or the Xbox is off, sends just fail and are retried."""

    KEEPALIVE_S = 0.03     # resend even if nothing changed (Feather times out at 0.1 s)
    STATUS_EVERY_S = 0.5
    RETRY_AFTER_ERROR_S = 0.25

    def __init__(self, bus, address, clock=time.monotonic):
        self.bus = bus
        self.address = address
        self.clock = clock
        self.fd = None
        self.seq = 0
        self.last_body = None
        self.last_send = 0.0
        self.last_status_read = float('-inf')   # read status on the first send
        self.next_retry = 0.0
        self.ok = False
        self.sent = 0
        self.errors = 0
        self.last_error = ''
        self.feather = None

    def _open(self):
        path = '/dev/i2c-%d' % self.bus
        fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(fd, I2C_SLAVE, self.address)
        self.fd = fd

    def _fail(self, exc, now):
        self.ok = False
        self.errors += 1
        self.last_error = str(exc)
        self.feather = None
        self.next_retry = now + self.RETRY_AFTER_ERROR_S
        if self.fd is not None and isinstance(exc, FileNotFoundError):
            self.close()

    def send(self, state):
        now = self.clock()
        if now < self.next_retry:
            return False
        body_key = (tuple(sorted(state.buttons)), round(state.lx, 5), round(state.ly, 5),
                    round(state.rx, 5), round(state.ry, 5), round(state.lt, 4), round(state.rt, 4))
        if body_key == self.last_body and (now - self.last_send) < self.KEEPALIVE_S and self.ok:
            return True
        try:
            if self.fd is None:
                self._open()
            self.seq = (self.seq + 1) & 0xFF
            os.write(self.fd, encode(state, self.seq))
            self.last_body = body_key
            self.last_send = now
            self.sent += 1
            self.ok = True
            if now - self.last_status_read >= self.STATUS_EVERY_S:
                self.last_status_read = now
                self.feather = decode_status(os.read(self.fd, STATUS_SIZE))
            return True
        except OSError as exc:
            self._fail(exc, now)
            return False

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
