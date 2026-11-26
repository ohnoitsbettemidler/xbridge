"""Settings: defaults, checking, loading and saving.

Everything here can be changed from the settings page. The file lives at
/etc/xbridge/config.json on the Pi.
"""

import copy
import json
import os
import tempfile

from .keycodes import ALL_CODES

PROFILES = ('foot', 'vehicle', 'air')

BUTTON_ACTIONS = ('A', 'B', 'X', 'Y', 'LB', 'RB', 'VIEW', 'MENU', 'LS', 'RS', 'GUIDE',
                  'DUP', 'DDOWN', 'DLEFT', 'DRIGHT')
TRIGGER_ACTIONS = ('LT', 'RT')
STICK_ACTIONS = ('LS_UP', 'LS_DOWN', 'LS_LEFT', 'LS_RIGHT', 'RS_UP', 'RS_DOWN', 'RS_LEFT', 'RS_RIGHT')
WHEEL_ACTIONS = tuple('WHEEL_%d' % i for i in range(1, 9))
SYSTEM_ACTIONS = ('WALK', 'PROFILE_FOOT', 'PROFILE_VEHICLE', 'PROFILE_AIR', 'TOGGLE_VEHICLE',
                  'PAUSE', 'SENS_UP', 'SENS_DOWN', 'DEADZONE_FINDER')
ALL_ACTIONS = BUTTON_ACTIONS + TRIGGER_ACTIONS + STICK_ACTIONS + WHEEL_ACTIONS + SYSTEM_ACTIONS

# Plain-English names for the settings page
ACTION_LABELS = {
    'A': 'A', 'B': 'B', 'X': 'X', 'Y': 'Y', 'LB': 'LB', 'RB': 'RB', 'LT': 'LT', 'RT': 'RT',
    'VIEW': 'View', 'MENU': 'Menu', 'LS': 'Left stick click', 'RS': 'Right stick click',
    'GUIDE': 'Xbox button', 'DUP': 'D-pad up', 'DDOWN': 'D-pad down', 'DLEFT': 'D-pad left',
    'DRIGHT': 'D-pad right',
    'LS_UP': 'Left stick up', 'LS_DOWN': 'Left stick down', 'LS_LEFT': 'Left stick left',
    'LS_RIGHT': 'Left stick right', 'RS_UP': 'Right stick up', 'RS_DOWN': 'Right stick down',
    'RS_LEFT': 'Right stick left', 'RS_RIGHT': 'Right stick right',
    'WALK': 'Walk / gentle (hold)', 'PROFILE_FOOT': 'Switch to on-foot',
    'PROFILE_VEHICLE': 'Switch to driving', 'PROFILE_AIR': 'Switch to flying',
    'TOGGLE_VEHICLE': 'Toggle on-foot / driving', 'PAUSE': 'Pause the bridge',
    'SENS_UP': 'Sensitivity +5%', 'SENS_DOWN': 'Sensitivity -5%',
    'DEADZONE_FINDER': 'Deadzone finder',
}
for _i in range(1, 9):
    ACTION_LABELS['WHEEL_%d' % _i] = 'Wheel slot %d' % _i

DEFAULTS = {
    'rate_hz': 200,
    'i2c_bus': 3,
    'i2c_address': 0x42,
    'keyboard_leds': True,
    'mouse': {
        'sensitivity': 1.0,          # looking around (on foot)
        'aim_sensitivity': 0.6,      # while the aim button is held
        'vehicle_sensitivity': 1.0,  # camera while driving / flying
        'vertical_ratio': 0.75,      # up/down speed compared with left/right
        'smoothing_ms': 16,          # averages mouse movement over this window
        'deadzone': 0.08,            # the game's dead patch in the middle of the stick
        'curve': 1.0,                # the game's stick response curve (1 = straight line)
        'invert_y': False,
        'ref_speed': 2000,           # mouse counts/second for a full-speed turn at sensitivity 1
    },
    'movement': {
        'walk_speed': 0.4,           # how far the stick goes while walk is held
        'steer_ramp_ms': 180,        # time from centre to full lock
        'steer_return_ms': 90,       # time back to centre after letting go
        'throttle_ramp_ms': 150,     # time to full throttle (RT) in vehicles
        'gentle_scale': 0.5,         # walk key in vehicles: throttle and steering scaled by this
    },
    'wheel': {
        'button': 'LB',              # held to open the weapon / loadout wheel
        'open_ms': 150,              # wait for the wheel to appear
        'select_ms': 100,            # hold the stick on the slot this long
        'angles': [0, 45, 90, 135, 180, 225, 270, 315],  # slot 1..8, degrees clockwise from up
    },
    'bindings': {
        'common': {
            'ESC': ['MENU'], 'V': ['VIEW'], 'M': ['VIEW'],
            'UP': ['DUP'], 'DOWN': ['DDOWN'], 'LEFT': ['DLEFT'], 'RIGHT': ['DRIGHT'],
            'HOME': ['GUIDE'], 'ENTER': ['A'], 'BACKSPACE': ['B'],
            'C': ['RS'], 'MOUSE_MIDDLE': ['RS'],
            'F1': ['PROFILE_FOOT'], 'F2': ['PROFILE_VEHICLE'], 'F3': ['PROFILE_AIR'],
            'F9': ['DEADZONE_FINDER'], 'F10': ['SENS_DOWN'], 'F11': ['SENS_UP'],
            'PAUSE': ['PAUSE'], 'F12': ['PAUSE'],
        },
        'foot': {
            'W': ['LS_UP'], 'S': ['LS_DOWN'], 'A': ['LS_LEFT'], 'D': ['LS_RIGHT'],
            'LEFTSHIFT': ['LS'], 'LEFTALT': ['WALK'],
            'SPACE': ['A'], 'LEFTCTRL': ['B'], 'R': ['X'], 'Q': ['RB'],
            'F': ['Y', 'TOGGLE_VEHICLE'], 'E': ['Y'],
            'MOUSE_LEFT': ['RT'], 'MOUSE_RIGHT': ['LT'],
            'TAB': ['LB'], 'Z': ['LS', 'RS'],
            'X': ['DLEFT'], 'MOUSE_SIDE': ['DLEFT'], 'MOUSE_EXTRA': ['DRIGHT'],
            '1': ['WHEEL_1'], '2': ['WHEEL_2'], '3': ['WHEEL_3'], '4': ['WHEEL_4'],
            '5': ['WHEEL_5'], '6': ['WHEEL_6'], '7': ['WHEEL_7'], '8': ['WHEEL_8'],
        },
        'vehicle': {
            'W': ['RT'], 'S': ['LT'], 'A': ['LS_LEFT'], 'D': ['LS_RIGHT'],
            'SPACE': ['A'], 'E': ['LS'], 'LEFTSHIFT': ['LS'], 'LEFTALT': ['WALK'],
            'LEFTCTRL': ['B'], 'F': ['Y', 'TOGGLE_VEHICLE'], 'R': ['X'],
            'MOUSE_LEFT': ['RB'], 'TAB': ['LB'], 'Q': ['DLEFT'],
        },
        'air': {
            'W': ['LS_UP'], 'S': ['LS_DOWN'], 'A': ['LS_LEFT'], 'D': ['LS_RIGHT'],
            'LEFTSHIFT': ['RT'], 'LEFTCTRL': ['LT'], 'Q': ['X'], 'E': ['B'],
            'G': ['LS'], 'MOUSE_LEFT': ['RB'], 'TAB': ['LB'],
            'SPACE': ['A'], 'LEFTALT': ['WALK'], 'F': ['Y', 'TOGGLE_VEHICLE'],
        },
    },
}

# (min, max) for every number the page can change
LIMITS = {
    'rate_hz': (50, 500),
    'i2c_bus': (0, 20),
    'i2c_address': (0x08, 0x77),
    'mouse.sensitivity': (0.05, 10.0),
    'mouse.aim_sensitivity': (0.05, 10.0),
    'mouse.vehicle_sensitivity': (0.05, 10.0),
    'mouse.vertical_ratio': (0.1, 3.0),
    'mouse.smoothing_ms': (0, 100),
    'mouse.deadzone': (0.0, 0.6),
    'mouse.curve': (0.3, 4.0),
    'mouse.ref_speed': (200, 20000),
    'movement.walk_speed': (0.05, 1.0),
    'movement.steer_ramp_ms': (0, 2000),
    'movement.steer_return_ms': (0, 2000),
    'movement.throttle_ramp_ms': (0, 2000),
    'movement.gentle_scale': (0.05, 1.0),
    'wheel.open_ms': (0, 1000),
    'wheel.select_ms': (10, 1000),
}


class ConfigError(ValueError):
    pass


def defaults():
    return copy.deepcopy(DEFAULTS)


def _merge(base, extra):
    """Fill in anything missing from `extra` with `base` (for older files)."""
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if k == 'bindings':
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(cfg):
    """Check a full config dict. Returns a cleaned copy or raises ConfigError
    with a message written for a person, not a programmer."""
    if not isinstance(cfg, dict):
        raise ConfigError('Settings must be a JSON object.')
    cfg = _merge(DEFAULTS, cfg)

    for path, (lo, hi) in LIMITS.items():
        parts = path.split('.')
        holder = cfg
        for p in parts[:-1]:
            holder = holder[p]
        val = holder[parts[-1]]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigError('%s must be a number.' % path)
        if not lo <= val <= hi:
            raise ConfigError('%s must be between %s and %s (got %s).' % (path, lo, hi, val))
    for k in ('rate_hz', 'i2c_bus', 'i2c_address'):
        cfg[k] = int(cfg[k])

    for flag in ('keyboard_leds',):
        cfg[flag] = bool(cfg[flag])
    cfg['mouse']['invert_y'] = bool(cfg['mouse']['invert_y'])

    wheel = cfg['wheel']
    if wheel['button'] not in BUTTON_ACTIONS:
        raise ConfigError('Wheel button must be one of: %s.' % ', '.join(BUTTON_ACTIONS))
    angles = wheel['angles']
    if not isinstance(angles, list) or len(angles) != 8 or \
            not all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in angles):
        raise ConfigError('Wheel angles must be a list of 8 numbers.')
    wheel['angles'] = [float(a) % 360 for a in angles]

    bindings = cfg.get('bindings')
    if not isinstance(bindings, dict):
        raise ConfigError('Bindings are missing.')
    clean = {}
    for section in ('common',) + PROFILES:
        table = bindings.get(section, {})
        if not isinstance(table, dict):
            raise ConfigError('Bindings for %s must be a table.' % section)
        out = {}
        for key, actions in table.items():
            if key not in ALL_CODES:
                raise ConfigError('Unknown key "%s" in %s bindings.' % (key, section))
            if isinstance(actions, str):
                actions = [actions]
            if not isinstance(actions, list) or not actions:
                raise ConfigError('Key %s in %s needs at least one action.' % (key, section))
            for a in actions:
                if a not in ALL_ACTIONS:
                    raise ConfigError('Unknown action "%s" on key %s.' % (a, key))
            out[key] = list(dict.fromkeys(actions))  # drop duplicates, keep order
        clean[section] = out
    cfg['bindings'] = clean
    return cfg


def load(path):
    """Load settings, falling back to defaults if the file is missing or bad.
    Returns (config, problem_message_or_None)."""
    if not os.path.exists(path):
        return defaults(), None
    try:
        with open(path) as f:
            return validate(json.load(f)), None
    except (OSError, ValueError) as exc:
        return defaults(), 'Settings file could not be used (%s); running on defaults.' % exc


def save(path, cfg):
    """Write atomically so a power cut mid-save can't leave a broken file."""
    folder = os.path.dirname(path) or '.'
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.config-', suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
