"""Tests for the Pi side of XBridge, plus the full chain through the real
firmware add-on code (compiled on the host with fake hardware).

Run from the pi folder:   python3 -m unittest discover -s tests -v
"""

import copy
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from xb import config as cfgmod                       # noqa: E402
from xb import link                                   # noqa: E402
from xb.evdev_lite import EVENT_FMT, parse_events     # noqa: E402
from xb.mapper import ControllerState, Mapper, stick_from_speed  # noqa: E402
from xb.web import Shared, WebServer                  # noqa: E402

PKG = os.path.abspath(os.path.join(HERE, '..', '..'))
FW_HEADER = os.environ.get('XB_FW_HEADER', os.path.join(PKG, 'firmware', 'src', 'pilink.h'))
HARNESS = os.environ.get('XB_HARNESS', os.path.join(PKG, 'firmware', 'test-harness', 'harness'))


def fresh_cfg(**mouse):
    c = cfgmod.defaults()
    c['mouse'].update(mouse)
    return c


def run_ticks(m, start, end, step=0.005, each=None):
    t = start
    state = None
    while t <= end + 1e-9:
        if each:
            each(t)
        state = m.tick(t)
        t += step
    return state


# --------------------------------------------------------------------- config
class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        c = cfgmod.validate(cfgmod.defaults())
        self.assertEqual(c['i2c_address'], 0x42)
        self.assertIn('W', c['bindings']['foot'])

    def test_rejects_unknown_key_and_action(self):
        c = cfgmod.defaults()
        c['bindings']['foot']['NOTAKEY'] = ['A']
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.validate(c)
        c = cfgmod.defaults()
        c['bindings']['foot']['W'] = ['JUMP']
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.validate(c)

    def test_rejects_out_of_range(self):
        c = cfgmod.defaults()
        c['mouse']['deadzone'] = 0.9
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.validate(c)

    def test_old_file_missing_fields_gets_defaults(self):
        c = cfgmod.validate({'mouse': {'sensitivity': 2.0}, 'bindings': {'foot': {'W': ['LS_UP']}}})
        self.assertEqual(c['mouse']['sensitivity'], 2.0)
        self.assertEqual(c['mouse']['deadzone'], cfgmod.DEFAULTS['mouse']['deadzone'])
        self.assertEqual(c['bindings']['foot'], {'W': ['LS_UP']})

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'sub', 'config.json')
            c = fresh_cfg(sensitivity=1.7)
            cfgmod.save(p, c)
            loaded, problem = cfgmod.load(p)
            self.assertIsNone(problem)
            self.assertEqual(loaded['mouse']['sensitivity'], 1.7)
            with open(p, 'w') as f:
                f.write('{broken')
            loaded, problem = cfgmod.load(p)
            self.assertIsNotNone(problem)
            self.assertEqual(loaded, cfgmod.defaults())


# ------------------------------------------------------------------ packet
class LinkTests(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(FW_HEADER), 'firmware source not alongside')
    def test_button_bits_match_firmware(self):
        with open(FW_HEADER) as f:
            src = f.read()
        fw = {m.group(1): int(m.group(2)) for m in
              re.finditer(r'#define PILINK_BTN_(\w+)\s+\(1u << (\d+)\)', src)}
        self.assertEqual(fw, link.BUTTON_BITS)
        self.assertIn('#define PI_LINK_PACKET_SIZE   %d' % link.PACKET_SIZE, src)
        self.assertIn('#define PI_LINK_CRC_INIT      0x%02X' % link.CRC_INIT, src)
        self.assertIn('#define PILINK_STATUS_MAGIC   0x%02X' % link.STATUS_MAGIC, src)

    def test_encode_layout(self):
        s = ControllerState({'A', 'DRIGHT'}, lx=1.0, ly=1.0, rx=-1.0, ry=-1.0, lt=1.0, rt=0.0)
        pkt = link.encode(s, 7)
        self.assertEqual(len(pkt), 14)
        self.assertEqual(pkt[0], 7)
        self.assertEqual(int.from_bytes(pkt[1:3], 'little'), (1 << 0) | (1 << 15))
        self.assertEqual(int.from_bytes(pkt[3:5], 'little'), 65535)   # full right
        self.assertEqual(int.from_bytes(pkt[5:7], 'little'), 0)       # full up
        self.assertEqual(int.from_bytes(pkt[7:9], 'little'), 0)       # full left
        self.assertEqual(int.from_bytes(pkt[9:11], 'little'), 65535)  # full down
        self.assertEqual((pkt[11], pkt[12]), (255, 0))
        self.assertEqual(pkt[13], link.crc8(pkt[:13]))

    def test_centre_is_centre(self):
        pkt = link.encode(ControllerState(), 0)
        for i in (3, 5, 7, 9):
            self.assertIn(int.from_bytes(pkt[i:i + 2], 'little'), (32767, 32768))

    def test_decode_status(self):
        self.assertIsNone(link.decode_status(b'\x00\x01\x00\x00'))
        s = link.decode_status(bytes([0xB7, 1, 0b1111, 3]))
        self.assertTrue(s['xbox_connected'] and s['xbox_authenticated'] and s['xbox_mode'] and s['receiving'])
        self.assertEqual(s['bad_packets'], 3)

    def test_link_survives_missing_bus(self):
        clock = [100.0]
        lk = link.I2CLink(bus=99, address=0x42, clock=lambda: clock[0])
        self.assertFalse(lk.send(ControllerState()))
        self.assertFalse(lk.ok)
        self.assertEqual(lk.errors, 1)
        self.assertFalse(lk.send(ControllerState()))   # inside retry window: no new attempt
        self.assertEqual(lk.errors, 1)
        clock[0] += 1
        lk.send(ControllerState())
        self.assertEqual(lk.errors, 2)

    def test_link_writes_and_reads_status(self):
        writes, reads = [], []
        lk = link.I2CLink(bus=3, address=0x42, clock=lambda: 0.0)
        lk.fd = 99
        real_write, real_read = link.os.write, link.os.read
        link.os.write = lambda fd, data: writes.append(bytes(data)) or len(data)
        link.os.read = lambda fd, n: reads.append(n) or bytes([0xB7, 1, 0x07, 0])
        try:
            self.assertTrue(lk.send(ControllerState({'B'})))
            self.assertTrue(lk.ok)
            self.assertTrue(lk.feather['xbox_authenticated'])
            self.assertTrue(lk.send(ControllerState({'B'})))   # unchanged, inside keepalive: skipped
            self.assertEqual(len(writes), 1)
        finally:
            link.os.write, link.os.read = real_write, real_read


# ------------------------------------------------------------------ mapper
class FootTests(unittest.TestCase):
    def setUp(self):
        self.m = Mapper(fresh_cfg())

    def test_wasd(self):
        m = self.m
        m.key('W', True, 0.0)
        s = m.tick(0.0)
        self.assertEqual((s.lx, s.ly), (0.0, 1.0))
        m.key('D', True, 0.01)
        s = m.tick(0.01)
        self.assertAlmostEqual(s.lx, math.sqrt(0.5))
        self.assertAlmostEqual(s.ly, math.sqrt(0.5))
        m.key('LEFTALT', True, 0.02)
        s = m.tick(0.02)
        self.assertAlmostEqual(math.hypot(s.lx, s.ly), 0.4)

    def test_last_pressed_wins(self):
        m = self.m
        m.key('A', True, 0.0)
        m.key('D', True, 0.1)
        self.assertEqual(m.tick(0.1).lx, 1.0)
        m.key('D', False, 0.2)
        self.assertEqual(m.tick(0.2).lx, -1.0)

    def test_buttons_and_triggers(self):
        m = self.m
        for k in ('SPACE', 'MOUSE_LEFT', 'MOUSE_RIGHT', 'Z'):
            m.key(k, True, 0.0)
        s = m.tick(0.0)
        self.assertTrue({'A', 'LS', 'RS'} <= s.buttons)
        self.assertEqual((s.lt, s.rt), (1.0, 1.0))

    def test_key_repeat_ignored(self):
        m = self.m
        m.key('W', True, 0.0)
        order = m.held['W']
        m.key('W', True, 0.1)
        self.assertEqual(m.held['W'], order)


class MouseTests(unittest.TestCase):
    def feed(self, m, counts_per_s, dur=0.2, step=0.001, dy=False):
        """Mouse reporting every `step` seconds at a steady speed; read the
        stick half a report after the last one (steady state)."""
        per = counts_per_s * step
        n = int(round(dur / step))
        for i in range(n):
            m.mouse_move(0 if dy else per, per if dy else 0, i * step)
        return m.tick((n - 1) * step + step / 2)

    def test_proportional_after_deadzone(self):
        dz = 0.1
        s1 = self.feed(Mapper(fresh_cfg(deadzone=dz)), 400)    # 0.2 of full speed
        s2 = self.feed(Mapper(fresh_cfg(deadzone=dz)), 800)    # 0.4 of full speed
        # Above the deadzone, stick travel doubles when mouse speed doubles
        self.assertAlmostEqual((s2.rx - dz) / (s1.rx - dz), 2.0, places=1)
        self.assertAlmostEqual(s1.rx, dz + (1 - dz) * 0.2, places=2)

    def test_tiny_movement_clears_deadzone(self):
        m = Mapper(fresh_cfg(deadzone=0.12))
        m.mouse_move(1, 0, 0.0)
        s = m.tick(0.001)
        self.assertGreater(s.rx, 0.12)
        self.assertLess(s.rx, 0.2)

    def test_stops_when_mouse_stops(self):
        m = Mapper(fresh_cfg())
        self.feed(m, 1000, dur=0.1)
        s = m.tick(0.1 + 0.05)   # well past the 16 ms window
        self.assertEqual((s.rx, s.ry), (0.0, 0.0))

    def test_full_speed_capped(self):
        s = self.feed(Mapper(fresh_cfg()), 50000)
        self.assertAlmostEqual(math.hypot(s.rx, s.ry), 1.0, places=6)

    def test_vertical_and_invert(self):
        s = self.feed(Mapper(fresh_cfg(deadzone=0.0)), 1000, dy=True)   # mouse pulled back
        self.assertLess(s.ry, 0)                                       # looks down
        self.assertAlmostEqual(s.ry, -0.5 * 0.75, places=2)
        s = self.feed(Mapper(fresh_cfg(deadzone=0.0, invert_y=True)), 1000, dy=True)
        self.assertGreater(s.ry, 0)

    def test_aim_and_vehicle_sensitivity(self):
        m = Mapper(fresh_cfg(deadzone=0.0, aim_sensitivity=0.5))
        m.key('MOUSE_RIGHT', True, 0.0)
        s = self.feed(m, 1000)
        self.assertAlmostEqual(s.rx, 0.25, places=2)
        m = Mapper(fresh_cfg(deadzone=0.0, vehicle_sensitivity=2.0))
        m.key('F2', True, 0.0)
        s = self.feed(m, 500)
        self.assertAlmostEqual(s.rx, 0.5, places=2)

    def test_curve_inverse(self):
        # With the game's curve undone, a game that squares the stick gets the
        # turn speed we asked for.
        for curve in (1.0, 2.0, 3.0):
            x, _ = stick_from_speed(0.3, 0, 0.1, curve)
            game_turn = ((x - 0.1) / 0.9) ** curve
            self.assertAlmostEqual(game_turn, 0.3, places=6)

    def test_no_smoothing_mode(self):
        m = Mapper(fresh_cfg(smoothing_ms=0, deadzone=0.0))
        m.tick(0.0)
        m.mouse_move(10, 0, 0.004)
        s = m.tick(0.005)          # 10 counts in 5 ms = 2000/s = full speed
        self.assertAlmostEqual(s.rx, 1.0, places=6)


class VehicleTests(unittest.TestCase):
    def test_throttle_and_steering_ramp(self):
        m = Mapper(fresh_cfg())
        m.key('F2', True, 0.0)
        m.key('F2', False, 0.0)
        m.tick(0.0)
        m.key('W', True, 0.0)
        m.key('D', True, 0.0)
        s = run_ticks(m, 0.005, 0.075)
        self.assertGreater(s.rt, 0.3)
        self.assertLess(s.rt, 0.7)                 # 150 ms ramp, 75 ms in
        self.assertGreater(s.lx, 0.2)
        self.assertLess(s.lx, 0.6)                 # 180 ms ramp
        s = run_ticks(m, 0.08, 0.3)
        self.assertEqual((s.rt, s.lx), (1.0, 1.0))
        m.key('D', False, 0.3)
        s = run_ticks(m, 0.305, 0.4)               # 90 ms return
        self.assertEqual(s.lx, 0.0)
        m.key('S', True, 0.4)
        self.assertEqual(m.tick(0.405).lt, 1.0)    # brake is instant

    def test_steer_through_centre(self):
        m = Mapper(fresh_cfg())
        m.key('F2', True, 0.0)
        m.key('D', True, 0.0)
        run_ticks(m, 0.0, 0.3)
        m.key('A', True, 0.3)                      # flick left
        s = run_ticks(m, 0.305, 0.35)
        self.assertGreater(s.lx, 0.0)              # still coming back through centre
        s = run_ticks(m, 0.355, 0.6)
        self.assertEqual(s.lx, -1.0)

    def test_gentle(self):
        m = Mapper(fresh_cfg())
        m.key('F2', True, 0.0)
        m.key('LEFTALT', True, 0.0)
        m.key('W', True, 0.0)
        s = run_ticks(m, 0.0, 0.5)
        self.assertAlmostEqual(s.rt, 0.5)


class ProfileTests(unittest.TestCase):
    def test_f_toggles_and_holds_y(self):
        m = Mapper(fresh_cfg())
        m.key('F', True, 0.0)
        s = m.tick(0.0)
        self.assertEqual(m.profile, 'vehicle')
        self.assertIn('Y', s.buttons)
        m.key('F', False, 0.1)
        m.key('F', True, 1.0)
        self.assertEqual(m.profile, 'foot')

    def test_function_keys(self):
        m = Mapper(fresh_cfg())
        m.key('F3', True, 0.0)
        self.assertEqual(m.profile, 'air')
        self.assertEqual(m.leds(), {'scroll': False, 'num': True, 'caps': False})
        m.key('F1', True, 0.1)
        self.assertEqual(m.profile, 'foot')

    def test_held_key_changes_meaning_with_profile(self):
        m = Mapper(fresh_cfg())
        m.key('W', True, 0.0)
        self.assertEqual(m.tick(0.0).ly, 1.0)
        m.key('F2', True, 0.01)
        s = run_ticks(m, 0.015, 0.5)
        self.assertEqual(s.ly, 0.0)
        self.assertEqual(s.rt, 1.0)

    def test_pause_releases_everything(self):
        m = Mapper(fresh_cfg())
        m.key('W', True, 0.0)
        m.key('MOUSE_LEFT', True, 0.0)
        m.key('PAUSE', True, 0.01)
        s = m.tick(0.02)
        self.assertEqual((s.buttons, s.ly, s.rt), (set(), 0.0, 0.0))
        self.assertTrue(m.leds()['caps'])
        m.key('PAUSE', False, 0.03)
        m.key('PAUSE', True, 0.04)
        self.assertEqual(m.tick(0.05).ly, 1.0)


class WheelTests(unittest.TestCase):
    def test_slot_macro(self):
        m = Mapper(fresh_cfg())
        m.key('3', True, 0.0)                 # slot 3 = 90 degrees = right
        s = m.tick(0.05)
        self.assertIn('LB', s.buttons)
        self.assertEqual((s.rx, s.ry), (0.0, 0.0))
        m.mouse_move(500, 0, 0.1)             # ignored during the macro
        s = m.tick(0.2)
        self.assertIn('LB', s.buttons)
        self.assertAlmostEqual(s.rx, 1.0)
        self.assertAlmostEqual(s.ry, 0.0, places=6)
        s = m.tick(0.26)                      # button let go, stick still on the slot
        self.assertNotIn('LB', s.buttons)
        self.assertAlmostEqual(s.rx, 1.0)
        s = m.tick(0.35)
        self.assertEqual((s.rx, s.ry), (0.0, 0.0))
        self.assertIsNone(m.macro)

    def test_scroll_tap(self):
        c = fresh_cfg()
        c['bindings']['foot']['WHEEL_UP'] = ['DUP']
        m = Mapper(c)
        m.scroll(1, 0.0)
        self.assertIn('DUP', m.tick(0.02).buttons)
        self.assertNotIn('DUP', m.tick(0.1).buttons)


class TuningTests(unittest.TestCase):
    def test_deadzone_finder(self):
        m = Mapper(fresh_cfg())
        m.key('F9', True, 0.0)
        m.key('F9', False, 0.05)
        s = m.tick(2.0)
        self.assertAlmostEqual(s.rx, 0.1, places=6)
        m.key('F9', True, 2.4)
        self.assertAlmostEqual(m.cfg['mouse']['deadzone'], 0.11, places=3)
        self.assertTrue(m.config_changed)
        self.assertEqual(m.tick(2.5).rx, 0.0)

    def test_sensitivity_keys(self):
        m = Mapper(fresh_cfg(sensitivity=1.0, aim_sensitivity=0.6))
        m.key('F11', True, 0.0)
        self.assertAlmostEqual(m.cfg['mouse']['sensitivity'], 1.05)
        m.key('MOUSE_RIGHT', True, 0.1)
        m.key('F10', True, 0.2)
        self.assertAlmostEqual(m.cfg['mouse']['aim_sensitivity'], round(0.6 / 1.05, 3))


# ----------------------------------------------------------- input parsing
class EvdevTests(unittest.TestCase):
    def test_parse_events(self):
        import struct
        raw = struct.pack(EVENT_FMT, 1, 2, 1, 17, 1) + struct.pack(EVENT_FMT, 1, 2, 2, 0, -5)
        self.assertEqual(parse_events(raw), [(1, 17, 1), (2, 0, -5)])


# ----------------------------------------------------------- settings page
class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'config.json')
        self.shared = Shared(cfgmod.defaults(), self.path)
        self.shared.status = {'profile': 'foot'}
        self.web = WebServer(self.shared, port=0, host='127.0.0.1')
        self.web.start()
        self.base = 'http://127.0.0.1:%d' % self.web.port

    def tearDown(self):
        self.web.stop()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.read()

    def post(self, body):
        req = urllib.request.Request(self.base + '/api/config', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_pages(self):
        code, body = self.get('/')
        self.assertEqual(code, 200)
        self.assertIn(b'XBridge', body)
        meta = json.loads(self.get('/api/meta')[1])
        self.assertIn('MOUSE_LEFT', meta['keys'])
        self.assertIn('WHEEL_1', meta['actions'])
        self.assertEqual(json.loads(self.get('/api/status')[1])['profile'], 'foot')

    def test_save_valid_and_reject_bad(self):
        cfg = json.loads(self.get('/api/config')[1])['config']
        cfg['mouse']['sensitivity'] = 2.5
        code, body = self.post(cfg)
        self.assertEqual(code, 200)
        self.assertTrue(body['ok'])
        with open(self.path) as f:
            self.assertEqual(json.load(f)['mouse']['sensitivity'], 2.5)
        self.assertEqual(self.shared.take_pending()['mouse']['sensitivity'], 2.5)
        cfg['mouse']['deadzone'] = 5
        code, body = self.post(cfg)
        self.assertEqual(code, 400)
        self.assertIn('deadzone', body['error'])


# ------------------------------------------------- through the firmware code
@unittest.skipUnless(os.path.exists(HARNESS), 'firmware harness not built')
class FirmwareChainTests(unittest.TestCase):
    """Pi packet -> real firmware add-on -> the numbers the Xbox report gets."""

    def run_harness(self, lines):
        out = subprocess.run([HARNESS], input='\n'.join(lines) + '\n', capture_output=True,
                             text=True, timeout=10, check=True).stdout
        return [json.loads(l) for l in out.strip().splitlines()]

    def test_full_chain(self):
        s = ControllerState({'A', 'MENU', 'GUIDE', 'DUP', 'DLEFT'}, lx=1.0, ly=1.0, rx=-0.5, ry=0.0, lt=1.0, rt=0.5)
        pkt = link.encode(s, 1).hex()
        res = self.run_harness(['time 0', 'setup', 'time 600000', 'write ' + pkt, 'time 610000', 'pre'])
        setup, pre = res[0], res[1]
        self.assertEqual(setup['addr'], 0x42)
        self.assertEqual(setup['usb5v'], 1)
        # GP2040 masks: B1=1, S2=1<<9, A1=1<<12; dpad UP=1, LEFT=4
        self.assertEqual(pre['buttons'], 1 | (1 << 9) | (1 << 12))
        self.assertEqual(pre['dpad'], 1 | 4)
        x = pre['xbox']
        self.assertEqual(x['lx'], 32767)       # full right
        self.assertEqual(x['ly'], 32767)       # full up (Xbox: up is positive)
        self.assertAlmostEqual(x['rx'] / 32768, -0.5, places=3)
        self.assertLessEqual(abs(x['ry']), 1)
        self.assertEqual(x['lt'], 1023)
        self.assertAlmostEqual(x['rt'] / 1023, 0.5, places=2)
        self.assertEqual(pre['led'], 1)

    def test_down_and_left_extremes(self):
        pkt = link.encode(ControllerState(lx=-1.0, ly=-1.0, rx=1.0, ry=-1.0), 2).hex()
        pre = self.run_harness(['time 0', 'setup', 'time 600000', 'write ' + pkt, 'pre'])[1]
        self.assertEqual(pre['xbox']['lx'], -32768)
        self.assertEqual(pre['xbox']['ly'], -32768)
        self.assertEqual(pre['xbox']['rx'], 32767)
        self.assertEqual(pre['xbox']['ry'], -32768)

    def test_late_interrupt_still_parsed(self):
        pkt = link.encode(ControllerState({'B'}), 3).hex()
        pre = self.run_harness(['time 0', 'setup', 'time 600000', 'writelate ' + pkt, 'pre'])[1]
        self.assertEqual(pre['buttons'], 2)

    def test_bad_packets_ignored_and_counted(self):
        good = link.encode(ControllerState({'X'}), 4)
        bad_crc = bytearray(good)
        bad_crc[13] ^= 0xFF
        res = self.run_harness(['time 0', 'setup', 'time 600000',
                                'write ' + bytes(bad_crc).hex(), 'pre',
                                'write ' + good[:10].hex(), 'pre',
                                'write ' + (good + b'\x00').hex(), 'pre',
                                'read 4'])
        for pre in res[1:4]:
            self.assertEqual(pre['buttons'], 0)
        self.assertEqual(res[4]['read'][0], 0xB7)
        self.assertEqual(res[4]['read'][3], 3)          # three bad packets counted

    def test_timeout_releases_everything(self):
        pkt = link.encode(ControllerState({'Y'}, lt=1.0), 5).hex()
        res = self.run_harness(['time 0', 'setup', 'time 600000', 'write ' + pkt,
                                'time 650000', 'pre', 'time 750000', 'pre'])
        self.assertEqual(res[1]['buttons'], 8)
        self.assertEqual((res[2]['buttons'], res[2]['lt']), (0, 0))
        self.assertEqual(res[2]['xbox']['lx'], -1)      # back to the driver's centre

    def test_boot_guard(self):
        pkt = link.encode(ControllerState({'MENU'}), 6).hex()
        res = self.run_harness(['time 0', 'setup', 'time 100000', 'write ' + pkt, 'pre'])
        self.assertEqual(res[1]['buttons'], 0)          # ignored straight after power-up

    def test_status_flags(self):
        pkt = link.encode(ControllerState(), 7).hex()
        res = self.run_harness(['time 0', 'setup', 'mounted 1', 'auth 1', 'time 600000',
                                'write ' + pkt, 'pre', 'read 4'])
        st = link.decode_status(bytes(res[2]['read']))
        self.assertTrue(st['xbox_connected'] and st['xbox_authenticated'] and st['receiving'])


if __name__ == '__main__':
    unittest.main()


# ------------------------------------------------------- whole program loop
class FakeDevice:
    """Looks like an InputDevice but reads raw input_event bytes from a pipe."""
    def __init__(self):
        self.r, self.w = os.pipe()
        os.set_blocking(self.r, False)
        self.fd = self.r
        self.path = '/dev/input/fake0'
        self.name = 'Fake keyboard+mouse'
        self.is_keyboard = self.is_mouse = True
        self.pressed = set()
        self.grabbed = False
        self.leds = []

    kind = 'keyboard + mouse'

    def read(self):
        try:
            data = os.read(self.r, 4096)
        except BlockingIOError:
            return []
        return parse_events(data)

    def set_led(self, led, on):
        self.leds.append((led, on))

    def send(self, *events):
        import struct
        os.write(self.w, b''.join(struct.pack(EVENT_FMT, 0, 0, t, c, v) for t, c, v in events))

    def close(self):
        os.close(self.r)
        os.close(self.w)


class CaptureLink:
    ok = True
    sent = errors = 0
    last_error = ''
    feather = None

    def __init__(self):
        self.states = []

    def send(self, state):
        self.states.append(state)
        self.sent += 1
        return True

    def close(self):
        pass


class BridgeLoopTests(unittest.TestCase):
    def test_events_flow_through_main_loop(self):
        import xbridge
        from xb.evdev_lite import EV_KEY, EV_REL, EV_SYN, REL_X
        tmp = tempfile.TemporaryDirectory()
        args = xbridge.argparse.Namespace(config=os.path.join(tmp.name, 'c.json'), port=0,
                                          no_grab=True, dry_run=True)
        bridge = xbridge.Bridge(args)
        dev = FakeDevice()
        bridge.inputs.scan = lambda: bridge.inputs.devices.setdefault(dev.fd, dev)
        cap = CaptureLink()
        bridge.link = cap
        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()
        try:
            time.sleep(0.3)
            dev.send((EV_KEY, 17, 1), (EV_SYN, 0, 0))                 # W down
            for _ in range(20):                                       # mouse moving right
                dev.send((EV_REL, REL_X, 6), (EV_SYN, 0, 0))
                time.sleep(0.003)
            moving = cap.states[-1]
            self.assertEqual(moving.ly, 1.0)
            # ~6 counts every ~3 ms is roughly full turning speed
            self.assertGreater(max(st.rx for st in cap.states), 0.6)
            dev.send((EV_KEY, 17, 0), (EV_KEY, 33, 1), (EV_SYN, 0, 0))   # W up, F down -> driving
            time.sleep(0.15)
            last = cap.states[-1]
            self.assertEqual((last.ly, last.rx), (0.0, 0.0))
            self.assertIn('Y', last.buttons)
            self.assertEqual(bridge.mapper.profile, 'vehicle')
            self.assertIn((2, True), dev.leds)                         # Scroll Lock light on
            deadline = time.time() + 3        # update rate is measured over a full second
            while time.time() < deadline and not bridge.shared.status.get('rate_hz'):
                time.sleep(0.05)
            st = bridge.shared.status
            self.assertEqual(st['profile'], 'vehicle')
            self.assertGreater(st['rate_hz'], 150)
        finally:
            bridge.stop()
            t.join(timeout=3)
            tmp.cleanup()
        self.assertFalse(t.is_alive())
        self.assertEqual(cap.states[-1].buttons, set())               # neutral sent on the way out


class UnplugTests(unittest.TestCase):
    def test_unplugged_keyboard_releases_held_keys(self):
        import xbridge
        tmp = tempfile.TemporaryDirectory()
        args = xbridge.argparse.Namespace(config=os.path.join(tmp.name, 'c.json'), port=0,
                                          no_grab=True, dry_run=True)
        bridge = xbridge.Bridge(args)

        class Gone(FakeDevice):
            def read(self):
                raise OSError(19, 'No such device')
        dev = Gone()
        bridge.inputs.devices[dev.fd] = dev
        bridge.mapper.key('W', True, 0.0)
        dev.pressed.add('W')
        self.assertEqual(bridge.mapper.tick(0.0).ly, 1.0)
        bridge.handle_device(dev.fd)
        self.assertNotIn(dev.fd, bridge.inputs.devices)
        self.assertEqual(bridge.mapper.tick(0.01).ly, 0.0)
        tmp.cleanup()
