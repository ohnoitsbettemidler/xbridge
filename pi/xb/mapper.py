"""Turns mouse and keyboard input into Xbox controller input, tuned for GTA VI.

This part has no hardware in it at all, so it can be tested on any computer:
feed it key presses and mouse movement with timestamps, call tick(), and it
returns the controller state to send.

How the mouse becomes a stick
-----------------------------
A mouse reports "moved this far". A stick means "keep turning at this speed".
So each tick we work out how fast the mouse is moving (averaged over a short
window, so the jitter of individual mouse reports doesn't make the camera
shudder) and turn that speed into a stick position.

Games don't move the camera in proportion to the stick: there's a dead patch
in the middle (the deadzone) and usually a curve so small pushes are extra
slow. We undo both, so twice the mouse speed gives twice the camera speed and
the smallest movement still gets past the dead patch.

The stick can only go so far, so very fast flicks are capped at the game's
full-stick turn speed. Turning the game's own look sensitivity up raises
that cap.
"""

import math
from collections import deque

from .config import BUTTON_ACTIONS, PROFILES

WHEEL_TAP_S = 0.06            # how long a scroll-wheel "press" lasts
WHEEL_RELEASE_HOLD_S = 0.05   # keep the stick on the slot briefly after letting go
FINDER_RATE = 0.05            # deadzone finder: stick moves 5% per second
FINDER_MAX = 0.6
SENS_STEP = 1.05


class ControllerState:
    __slots__ = ('buttons', 'lx', 'ly', 'rx', 'ry', 'lt', 'rt')

    def __init__(self, buttons=None, lx=0.0, ly=0.0, rx=0.0, ry=0.0, lt=0.0, rt=0.0):
        self.buttons = set(buttons or ())
        self.lx, self.ly, self.rx, self.ry = lx, ly, rx, ry   # -1..1, y: +1 = up
        self.lt, self.rt = lt, rt                              # 0..1

    def as_dict(self):
        return {'buttons': sorted(self.buttons), 'lx': round(self.lx, 3), 'ly': round(self.ly, 3),
                'rx': round(self.rx, 3), 'ry': round(self.ry, 3),
                'lt': round(self.lt, 3), 'rt': round(self.rt, 3)}

    def __repr__(self):
        return 'ControllerState(%r)' % self.as_dict()


def stick_from_speed(nx, ny, deadzone, curve):
    """Normalised mouse speed (1.0 = full-speed turn) to a stick position that
    makes the game turn at that speed, undoing its deadzone and curve."""
    m = math.hypot(nx, ny)
    if m < 1e-9:
        return 0.0, 0.0
    turn = min(m, 1.0)
    out = deadzone + (1.0 - deadzone) * (turn ** (1.0 / curve))
    out = min(out, 1.0)
    return nx / m * out, ny / m * out


def _move_toward(current, target, step):
    if current < target:
        return min(target, current + step)
    return max(target, current - step)


class Mapper:
    def __init__(self, cfg):
        self.cfg = cfg
        self.profile = 'foot'
        self.paused = False
        self.held = {}             # key name -> order number it was pressed in
        self.order = 0
        self.momentary = {}        # scroll wheel pseudo-keys -> release time
        self.motion = deque()      # (time, dx, dy)
        self.motion_since_tick = [0, 0]
        self.last_tick = None
        self.steer = 0.0
        self.throttle = 0.0
        self.macro = None          # weapon wheel in progress
        self.finder = None         # deadzone finder in progress
        self.config_changed = False
        self.messages = deque(maxlen=8)
        self._rebuild()

    # ------------------------------------------------------------------ setup
    def set_config(self, cfg):
        self.cfg = cfg
        self._rebuild()

    def _rebuild(self):
        b = self.cfg['bindings']
        self.tables = {}
        for p in PROFILES:
            t = dict(b.get('common', {}))
            t.update(b.get(p, {}))
            self.tables[p] = t

    @property
    def table(self):
        return self.tables[self.profile]

    def say(self, text):
        self.messages.append(text)

    # ------------------------------------------------------------------ input
    def key(self, name, down, now):
        if down:
            if name in self.held:
                return                      # key repeat - ignore
            self.order += 1
            self.held[name] = self.order
            self._on_press(name, now)
        else:
            self.held.pop(name, None)

    def scroll(self, steps, now):
        name = 'WHEEL_UP' if steps > 0 else 'WHEEL_DOWN'
        for _ in range(min(abs(int(steps)), 3)):
            self.order += 1
            self.momentary[name] = now + WHEEL_TAP_S
            self._on_press(name, now)

    def mouse_move(self, dx, dy, now):
        if self.paused or self.macro or self.finder:
            return
        self.motion.append((now, dx, dy))
        self.motion_since_tick[0] += dx
        self.motion_since_tick[1] += dy

    def release_all(self):
        """Forget every held key (e.g. a keyboard was unplugged)."""
        self.held.clear()
        self.momentary.clear()
        self.motion.clear()
        self.motion_since_tick = [0, 0]
        self.steer = self.throttle = 0.0

    def release_keys(self, names):
        for n in names:
            self.held.pop(n, None)

    # ---------------------------------------------------------- one-shot keys
    def _on_press(self, name, now):
        actions = self.table.get(name, ())
        if 'PAUSE' in actions:
            self.paused = not self.paused
            self.macro = self.finder = None
            self.motion.clear()
            self.say('Bridge paused - controller released' if self.paused else 'Bridge running')
            return
        if self.paused:
            return
        for a in actions:
            if a == 'PROFILE_FOOT':
                self._set_profile('foot')
            elif a == 'PROFILE_VEHICLE':
                self._set_profile('vehicle')
            elif a == 'PROFILE_AIR':
                self._set_profile('air')
            elif a == 'TOGGLE_VEHICLE':
                self._set_profile('vehicle' if self.profile == 'foot' else 'foot')
            elif a.startswith('WHEEL_') and self.macro is None and self.finder is None:
                self.macro = {'start': now, 'slot': int(a[-1])}
                self.motion.clear()
            elif a in ('SENS_UP', 'SENS_DOWN'):
                self._nudge_sensitivity(SENS_STEP if a == 'SENS_UP' else 1.0 / SENS_STEP)
            elif a == 'DEADZONE_FINDER':
                self._toggle_finder(now)

    def _set_profile(self, profile):
        if profile != self.profile:
            self.profile = profile
            self.steer = self.throttle = 0.0
            self.say({'foot': 'On foot', 'vehicle': 'Driving', 'air': 'Flying'}[profile])

    def _sens_key(self):
        if self.profile in ('vehicle', 'air'):
            return 'vehicle_sensitivity'
        if self._aiming():
            return 'aim_sensitivity'
        return 'sensitivity'

    def _nudge_sensitivity(self, factor):
        key = self._sens_key()
        m = self.cfg['mouse']
        m[key] = round(max(0.05, min(10.0, m[key] * factor)), 3)
        self.config_changed = True
        self.say('%s now %.2f' % (key.replace('_', ' '), m[key]))

    def _toggle_finder(self, now):
        if self.finder is None:
            self.finder = {'start': now}
            self.macro = None
            self.motion.clear()
            self.say('Deadzone finder: press F9 again the moment the camera starts to move')
        else:
            value = min(FINDER_MAX, (now - self.finder['start']) * FINDER_RATE)
            self.finder = None
            dz = round(max(0.0, value - 0.01), 3)
            self.cfg['mouse']['deadzone'] = dz
            self.config_changed = True
            self.say('Deadzone set to %.3f' % dz)

    # ------------------------------------------------------------ evaluation
    def _actions_held(self, now):
        """{action: latest press order} for everything currently held."""
        out = {}
        table = self.table
        for name, order in self.held.items():
            for a in table.get(name, ()):
                if out.get(a, -1) < order:
                    out[a] = order
        for name, until in list(self.momentary.items()):
            if now >= until:
                del self.momentary[name]
                continue
            for a in table.get(name, ()):
                out[a] = max(out.get(a, -1), self.order)
        return out

    def _aiming(self):
        if self.profile != 'foot':
            return False
        table = self.table
        return any('LT' in table.get(n, ()) for n in self.held)

    @staticmethod
    def _axis(held, neg, pos):
        """Last-pressed-wins for opposite directions."""
        n, p = held.get(neg), held.get(pos)
        if n is not None and p is not None:
            return -1.0 if n > p else 1.0
        if n is not None:
            return -1.0
        if p is not None:
            return 1.0
        return 0.0

    def _mouse_stick(self, now, dt):
        m = self.cfg['mouse']
        window = m['smoothing_ms'] / 1000.0
        if window > 0:
            cutoff = now - window
            while self.motion and self.motion[0][0] <= cutoff:
                self.motion.popleft()
            sx = sum(e[1] for e in self.motion)
            sy = sum(e[2] for e in self.motion)
            vx, vy = sx / window, sy / window
        else:
            vx = self.motion_since_tick[0] / dt if dt > 0 else 0.0
            vy = self.motion_since_tick[1] / dt if dt > 0 else 0.0
            self.motion.clear()
        self.motion_since_tick = [0, 0]

        sens = m[self._sens_key()]
        nx = vx * sens / m['ref_speed']
        ny = vy * sens * m['vertical_ratio'] / m['ref_speed']
        # Mouse forward (dy < 0) looks up, which is stick up (+y)
        ny = ny if m['invert_y'] else -ny
        return stick_from_speed(nx, ny, m['deadzone'], m['curve'])

    def tick(self, now):
        dt = 0.0 if self.last_tick is None else max(0.0, now - self.last_tick)
        self.last_tick = now
        state = ControllerState()

        if self.paused:
            self.motion.clear()
            self.motion_since_tick = [0, 0]
            return state

        held = self._actions_held(now)
        mv = self.cfg['movement']
        gentle = 'WALK' in held

        for a in held:
            if a in BUTTON_ACTIONS:
                state.buttons.add(a)

        # Left stick
        lx = self._axis(held, 'LS_LEFT', 'LS_RIGHT')
        ly = self._axis(held, 'LS_DOWN', 'LS_UP')
        if self.profile == 'vehicle':
            target = lx * (mv['gentle_scale'] if gentle else 1.0)
            # Letting go, or switching from left to right: head back to the
            # centre at the (quicker) return speed first, then ramp out.
            crossing = self.steer != 0.0 and target != 0.0 and (target > 0) != (self.steer > 0)
            if target == 0.0 or crossing:
                ramp, goal = mv['steer_return_ms'], 0.0
            else:
                ramp, goal = mv['steer_ramp_ms'], target
            step = 1.0 if ramp <= 0 else dt / (ramp / 1000.0)
            self.steer = _move_toward(self.steer, goal, step)
            state.lx = self.steer
            state.ly = ly
        else:
            if lx and ly:
                lx *= math.sqrt(0.5)
                ly *= math.sqrt(0.5)
            scale = 1.0
            if gentle:
                scale = mv['walk_speed'] if self.profile == 'foot' else mv['gentle_scale']
            state.lx, state.ly = lx * scale, ly * scale

        # Triggers
        rt_target = 1.0 if 'RT' in held else 0.0
        if self.profile in ('vehicle', 'air') and gentle:
            rt_target *= mv['gentle_scale']
        if self.profile == 'vehicle' and mv['throttle_ramp_ms'] > 0 and rt_target > self.throttle:
            self.throttle = _move_toward(self.throttle, rt_target, dt / (mv['throttle_ramp_ms'] / 1000.0))
        else:
            self.throttle = rt_target
        state.rt = self.throttle
        state.lt = 1.0 if 'LT' in held else 0.0

        # Right stick: wheel macro / deadzone finder / keys / mouse
        if self.macro is not None:
            self._run_macro(state, now)
        elif self.finder is not None:
            value = (now - self.finder['start']) * FINDER_RATE
            if value >= FINDER_MAX:
                self.finder = None
                self.say('Deadzone finder gave up - is the game camera moving at all?')
            else:
                state.rx = value
        else:
            kx = self._axis(held, 'RS_LEFT', 'RS_RIGHT')
            ky = self._axis(held, 'RS_DOWN', 'RS_UP')
            if kx or ky:
                if kx and ky:
                    kx *= math.sqrt(0.5)
                    ky *= math.sqrt(0.5)
                state.rx, state.ry = kx, ky
                self.motion.clear()
                self.motion_since_tick = [0, 0]
            else:
                state.rx, state.ry = self._mouse_stick(now, dt)

        return state

    def _run_macro(self, state, now):
        w = self.cfg['wheel']
        t = now - self.macro['start']
        open_s, select_s = w['open_ms'] / 1000.0, w['select_ms'] / 1000.0
        angle = math.radians(w['angles'][self.macro['slot'] - 1])
        if t < open_s:
            state.buttons.add(w['button'])
        elif t < open_s + select_s:
            state.buttons.add(w['button'])
            state.rx, state.ry = math.sin(angle), math.cos(angle)
        elif t < open_s + select_s + WHEEL_RELEASE_HOLD_S:
            state.buttons.discard(w['button'])
            state.rx, state.ry = math.sin(angle), math.cos(angle)
        else:
            self.macro = None
            self.motion.clear()
            self.motion_since_tick = [0, 0]

    # --------------------------------------------------------------- status
    def leds(self):
        return {'scroll': self.profile == 'vehicle', 'num': self.profile == 'air', 'caps': self.paused}

    def status(self):
        return {
            'profile': self.profile,
            'paused': self.paused,
            'aiming': self._aiming(),
            'wheel_running': self.macro is not None,
            'finder_running': self.finder is not None,
            'messages': list(self.messages),
        }
