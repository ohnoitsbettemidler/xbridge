#!/usr/bin/env python3
"""XBridge case: Raspberry Pi 3 B+ + Adafruit Feather RP2040 USB Host + Mayflash Magic-X.

An original retro-console design: pixel-dither vents, engraved pixel badge,
V-groove stripes, a front bay the Magic-X slides into, a pixel status lens
over the Feather's link light and a real BOOT button on top.

Parts (all print without supports):
  lid      - print upside down (roof on the bed)
  base     - print as is (floor on the bed)
  lens     - print head-down, ideally clear/natural filament
  boot_key - print head-down

Coordinates: X to the right, Y towards the back, Z up; front-left-bottom
outer corner of the case is (0, 0, 0). All sizes in mm.

Board data:
  Pi 3 B+  - official Raspberry Pi mechanical drawing (RP-008337-DS-2)
  Feather  - Adafruit's own 3D model and Eagle board file (product 5723)
  Magic-X  - Mayflash spec, 73 x 21 x 8.7 mm
"""

import math
import os
import sys

import numpy as np
import trimesh
from manifold3d import CrossSection as CS
from manifold3d import FillRule, JoinType
from manifold3d import Manifold as M
from manifold3d import set_circular_segments

set_circular_segments(64)

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, 'ref', 'PressStart2P-Regular.ttf')
FEATHER_STL = os.path.join(HERE, 'ref', 'cad', '5723 Feather RP2040 USB Host',
                           '5723 Feather RP2040 USB Host.stl')

# ----------------------------------------------------------------- settings
WALL = 2.0            # side walls
ROOF = 2.4
FLOOR = 2.4           # base plate
R_CORNER = 4.0        # vertical corner radius
TOP_CHAMFER = 3.0
BOT_CHAMFER = 0.6
H = 33.0              # top of the lid
ROOF_UNDER = H - ROOF
FIT = 0.25            # gap between base plate and lid walls
RIM_T, RIM_H = 1.6, 1.2
STANDOFF = 4.0
PCB_Z = FLOOR + STANDOFF          # underside of both boards

# Feather placement: USB-C at the back, USB-A towards the front
FX0 = 6.6                          # case X of the Feather's fy = 0 edge
FEATHER_W = 22.86
FEATHER_L = 50.8
FEATHER_T = 1.57
# Pi placement: USB/Ethernet at the back, power/HDMI on the right
PI_L, PI_W, PI_T = 85.0, 56.0, 1.4
PI_X0 = FX0 + FEATHER_W + 8.0      # 8 mm channel between the boards (screw boss)
W = PI_X0 + PI_W + 0.4 + WALL      # 0.4 gap between Pi edge and right wall
D = 116.0
FY0 = D - 5.4                      # case Y of the Feather's fx = 0 edge
PI_Y0 = D - 87.4                   # case Y of the Pi's SD-card end (px = 0)
PI_TOP = PCB_Z + PI_T

# Magic-X (73 long incl. ~12 mm plug, 21 x 8.7 body)
DONGLE_W, DONGLE_H, DONGLE_BODY = 21.0, 8.7, 61.0
USB_A_FACE_FX = 54.28              # from Adafruit's model
USB_A_Z = PCB_Z + (0.79 + 8.14) / 2  # centre of the USB-A jack
DONGLE_X = FX0 + 11.43             # centre of the USB-A jack across the board
SLOT_W, SLOT_H = 23.0, 11.0
SLOT_X0, SLOT_X1 = DONGLE_X - SLOT_W / 2, DONGLE_X + SLOT_W / 2
SLOT_Z0 = round(USB_A_Z - SLOT_H / 2, 2)
SLOT_Z1 = SLOT_Z0 + SLOT_H

# Lid screw bosses (M3 from underneath)
BOSS_R = 3.5
BOSSES = [(35.5, 5.0), (W - 5.0, 5.0), (FX0 + FEATHER_W + 4.0, D - 5.5)]

# Feather features (fx, fy in board coordinates)
LED_F = (1.82, 17.9)               # red link LED (pin 13), lens offset slightly off USB-C
BOOT_F = (15.875, 5.08)            # BOOT button
BOOT_TOP = PCB_Z + 3.47           # top of the round actuator (Adafruit model)

STRIPES_Z = (21.0, 24.0, 27.0)


def pi_xy(px, py):
    return PI_X0 + PI_W - py, PI_Y0 + px


def f_xy(fx, fy):
    return FX0 + fy, FY0 - fx


# ----------------------------------------------------------------- helpers
def box(x0, x1, y0, y1, z0, z1):
    return M.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def cyl(x, y, z0, z1, r, r_top=None, seg=64):
    return M.cylinder(z1 - z0, r, r if r_top is None else r_top, seg).translate([x, y, z0])


def rrect(x0, y0, x1, y1, r):
    r = min(r, (x1 - x0) / 2 - 1e-6, (y1 - y0) / 2 - 1e-6)
    return CS.square([x1 - x0 - 2 * r, y1 - y0 - 2 * r]).translate([x0 + r, y0 + r]).offset(r, JoinType.Round)


def top_round_rect(u0, u1, v0, v1, r):
    """Rectangle in a (u, v) plane with only the top (v1) corners rounded."""
    return rrect(u0, v0 - r - 1, u1, v1, r) ^ CS.square([u1 - u0, v1 - v0]).translate([u0, v0])


def extrude(cs, z0, z1):
    return cs.extrude(z1 - z0).translate([0, 0, z0])


def in_xz(cs, y0, y1):
    """Shape drawn in the (x, z) plane, pushed through y0..y1 (front/back walls)."""
    return cs.extrude(y1 - y0).rotate([90, 0, 0]).translate([0, y1, 0])


def in_yz(cs, x0, x1):
    """Shape drawn in the (y, z) plane, pushed through x0..x1 (side walls)."""
    return cs.extrude(x1 - x0).rotate([90, 0, 0]).rotate([0, 0, 90]).translate([x0, 0, 0])


def text_cs(text, height, center, mirror=False):
    """Pixel-font text as a 2D shape; `height` = cap height in mm."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    tp = TextPath((0, 0), text, size=100, prop=FontProperties(fname=FONT))
    polys = [p for p in tp.to_polygons() if len(p) >= 3]
    cs = CS([[(float(x), float(y)) for x, y in p] for p in polys], FillRule.EvenOdd)
    (x0, y0), (x1, y1) = cs.bounds()[:2], cs.bounds()[2:]
    s = height / (y1 - y0)
    cs = cs.translate([-(x0 + x1) / 2, -(y0 + y1) / 2]).scale([s, s])
    # pixels that touch only at a corner are merged with a hair of overlap,
    # so the engraving is a clean solid for slicers
    cs = cs.offset(0.04, JoinType.Miter)
    if mirror:
        cs = cs.mirror([1, 0])
    return cs.translate(list(center))


OUTLINE = rrect(0, 0, W, D, R_CORNER)
INNER = rrect(WALL, WALL, W - WALL, D - WALL, R_CORNER - WALL)
BASE_OUTLINE = INNER.offset(-FIT, JoinType.Round)


# ----------------------------------------------------------------- port openings
# (name, wall, u0, u1, v0 = bottom/sill height, v1 = top, corner radius)
# u is X for the back/front walls and Y for the right wall.
def _pi_back(py0, py1, height, clr=0.8):
    x0, _ = pi_xy(0, py1)
    x1, _ = pi_xy(0, py0)
    return x0 - clr, x1 + clr, PI_TOP - 1.0, PI_TOP + height + clr


def _pi_right(px0, px1, height, clr=1.0):
    _, y0 = pi_xy(px0, 0)
    _, y1 = pi_xy(px1, 0)
    return y0 - clr, y1 + clr, PI_TOP - 1.0, PI_TOP + height + clr


USBC_X, _ = f_xy(0, 11.43)
USBC_Z = PCB_Z + (0.57 + 4.77) / 2
MICRO_USB_Y = pi_xy(10.6, 0)[1]
MICRO_USB_Z = PI_TOP + 1.5
AUDIO_Y = pi_xy(53.5, 0)[1]
AUDIO_Z = PI_TOP + 3.0
PORTS = [
    ('ethernet', 'back', *_pi_back(2.5, 18.1, 13.5), 1.2),
    ('usb_low', 'back', *_pi_back(22.4, 35.5, 16.0), 1.2),
    ('usb_high', 'back', *_pi_back(40.5, 53.6, 16.0), 1.2),
    # Feather USB-C sits 4.2 mm inside, so its opening is cable-plug sized
    ('feather_usbc', 'back', USBC_X - 6.25, USBC_X + 6.25, USBC_Z - 3.75, USBC_Z + 3.75, 3.0),
    ('power', 'right', MICRO_USB_Y - 5.75, MICRO_USB_Y + 5.75, MICRO_USB_Z - 3.75, MICRO_USB_Z + 3.75, 2.5),
    ('hdmi', 'right', *_pi_right(24.8, 39.3, 6.5), 1.5),
    ('audio', 'right', AUDIO_Y - 3.8, AUDIO_Y + 3.8, PI_TOP - 1.0, AUDIO_Z + 3.8, 3.8),
]


def port_notch(wall, u0, u1, v0, v1, r):
    """Opening that runs down to the bottom edge of the lid (the base fills the
    part below v0), so the lid can be lowered over ports that poke into the wall."""
    cs = top_round_rect(u0, u1, -2.0, v1, r)
    if wall == 'back':
        return in_xz(cs, D - WALL - 0.6, D + 1)
    return in_yz(cs, W - WALL - 0.6, W + 1)


def port_sill(wall, u0, u1, v0):
    u0, u1 = u0 + 0.2, u1 - 0.2
    if wall == 'back':
        return box(u0, u1, D - WALL - 0.4, D, 0, FLOOR) + box(u0, u1, D - WALL + 0.1, D, 0, v0)
    return box(W - WALL - 0.4, W, u0, u1, 0, FLOOR) + box(W - WALL + 0.1, W, u0, u1, 0, v0)


# ----------------------------------------------------------------- lid
def body():
    return M.batch_hull([
        extrude(OUTLINE.offset(-BOT_CHAMFER, JoinType.Round), 0, 0.01),
        extrude(OUTLINE, BOT_CHAMFER, H - TOP_CHAMFER),
        extrude(OUTLINE.offset(-TOP_CHAMFER, JoinType.Round), H - 0.01, H),
    ])


def stripes():
    """45-degree V-grooves (0.8 deep) around the front and sides. The groove is
    'everything outside a pinched waist': the waist is two frustums meeting at
    the groove's deepest line, so both flanks print as 45-degree slopes."""
    cut = M()
    depth = 0.8
    for zc in STRIPES_Z:
        mid = extrude(OUTLINE.offset(-depth, JoinType.Round), zc - 0.001, zc + 0.001)
        lo = extrude(OUTLINE.offset(0.05, JoinType.Round), zc - depth - 0.05 - 0.001, zc - depth - 0.05)
        hi = extrude(OUTLINE.offset(0.05, JoinType.Round), zc + depth + 0.05, zc + depth + 0.05 + 0.001)
        waist = M.batch_hull([lo, mid]) + M.batch_hull([mid, hi])
        band = extrude(OUTLINE.offset(1.0, JoinType.Round), zc - depth, zc + depth)
        cut += band - waist
    # stripes run along the front and both sides, stopping short of the back
    return cut ^ box(-5, W + 5, -5, D - 16, 0, H)


def vents():
    """Pixel 'dither' vents over the Pi: big squares at the back fading to
    small ones at the front, every other row shifted half a step."""
    holes = []
    pitch = 5.0
    x_lo, x_hi = PI_X0 + 4.5, W - 6.0
    y_back, y_front = D - 12.5, PI_Y0 + 9.0
    rows = int((y_back - y_front) // pitch) + 1
    for i in range(rows):
        y = y_back - i * pitch
        size = 3.4 - (3.4 - 1.0) * i / max(1, rows - 1)
        off = (pitch / 2) if i % 2 else 0.0
        n = int((x_hi - x_lo - off) // pitch) + 1
        for j in range(n):
            x = x_lo + off + j * pitch
            holes.append(box(x - size / 2, x + size / 2, y - size / 2, y + size / 2, ROOF_UNDER - 1, H + 1))
    return M.batch_boolean(holes, OpType_add())


def OpType_add():
    from manifold3d import OpType
    return OpType.Add


def badge():
    cs = text_cs('XBRIDGE', 7.0, (W / 2, 15.0))
    # small labels beside the status lens and the BOOT key, left-aligned
    for word, (x, y) in (('LINK', (LENS_XY[0] + LENS_HEAD / 2 + 2.5, LENS_XY[1])),
                         ('BOOT', (BOOT_XY[0] + KEY_HEAD / 2 + 2.5, BOOT_XY[1]))):
        t = text_cs(word, 4.0, (0, 0))
        (x0, _), = [t.bounds()[:2]]
        cs += t.translate([x - x0, y])
    return extrude(cs, H - 0.6, H + 1)


LENS_XY = f_xy(*LED_F)
BOOT_XY = f_xy(*BOOT_F)
LENS_HEAD, LENS_RECESS = 6.8, 1.0
KEY_HEAD, KEY_RECESS, KEY_GAP = 6.8, 1.2, 1.0


def lid():
    shell = body() - extrude(INNER, -1, ROOF_UNDER)

    # screw bosses (M3 self-tapping, 2.6 pilot)
    for (x, y) in BOSSES:
        shell += cyl(x, y, FLOOR, ROOF_UNDER + 0.5, BOSS_R) ^ extrude(INNER, -1, H)
        shell -= cyl(x, y, FLOOR - 0.5, FLOOR + 14, 1.3)

    # guide tubes under the roof for the lens and the BOOT key
    lx, ly = LENS_XY
    bx, by = BOOT_XY
    shell += cyl(lx, ly, ROOF_UNDER - 6, ROOF_UNDER + 0.5, 3.2)
    shell += cyl(bx, by, ROOF_UNDER - 8, ROOF_UNDER + 0.5, 3.0)
    shell -= cyl(lx, ly, ROOF_UNDER - 7, H + 1, 1.8)
    shell -= box(lx - (LENS_HEAD + 0.4) / 2, lx + (LENS_HEAD + 0.4) / 2,
                 ly - (LENS_HEAD + 0.4) / 2, ly + (LENS_HEAD + 0.4) / 2, H - LENS_RECESS, H + 1)
    shell -= cyl(bx, by, ROOF_UNDER - 9, H + 1, 1.6)
    shell -= box(bx - (KEY_HEAD + 0.6) / 2, bx + (KEY_HEAD + 0.6) / 2,
                 by - (KEY_HEAD + 0.6) / 2, by + (KEY_HEAD + 0.6) / 2, H - KEY_RECESS, H + 1)

    # ports
    for (_, wall, u0, u1, v0, v1, r) in PORTS:
        shell -= port_notch(wall, u0, u1, v0, v1, r)

    # front bay + Magic-X slot (slot runs to the bottom edge; the base fills below)
    bay = top_round_rect(SLOT_X0 - 2.0, SLOT_X1 + 2.0, SLOT_Z0, SLOT_Z1 + 3.0, 3.0)
    # 1 mm deep, with a 45-degree lower edge so it prints upside down without support
    shell -= M.batch_hull([in_xz(bay, -1, 1.0), in_xz(bay, -1, 0.0).translate([0, 0, -1.0])])
    shell -= in_xz(top_round_rect(SLOT_X0, SLOT_X1, -2.0, SLOT_Z1, 2.0), -1, WALL + 0.6)

    shell -= stripes()
    shell -= vents()
    shell -= badge()
    return shell


# ----------------------------------------------------------------- base
PI_HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]
F_SCREWS = [(2.54, 2.54), (2.54, 20.32)]
F_PEGS = [(48.26, 2.54), (48.26, 20.32)]
FEET = [(12.0, 14.0), (W - 12.0, 16.0), (12.0, D - 12.0), (W - 12.0, D - 12.0)]


def base():
    plate = M.batch_hull([
        extrude(BASE_OUTLINE.offset(-0.4, JoinType.Round), 0, 0.01),
        extrude(BASE_OUTLINE, 0.4, FLOOR),
    ])
    rim = extrude(BASE_OUTLINE - BASE_OUTLINE.offset(-RIM_T, JoinType.Round), FLOOR - 0.01, FLOOR + RIM_H)
    for (x, y) in BOSSES:
        rim -= cyl(x, y, 0, 20, BOSS_R + 0.5)
    part = plate + rim

    # port sills fill the lid's notches up to the bottom of each opening
    for (_, wall, u0, u1, v0, v1, r) in PORTS:
        part += port_sill(wall, u0, u1, v0)

    # Magic-X: front sill + trough the dongle slides along, with side rails
    gx0, gx1 = SLOT_X0 + 0.2, SLOT_X1 - 0.2
    part += box(gx0, gx1, 0, 55.0, 0, SLOT_Z0)
    rail_y0, rail_y1 = WALL + 0.3, 50.0
    part += box(gx0 - 1.2, gx0, rail_y0, rail_y1, FLOOR - 0.01, SLOT_Z0 + 5.0)
    part += box(gx1, gx1 + 1.2, rail_y0, rail_y1, FLOOR - 0.01, SLOT_Z0 + 5.0)

    # Pi standoffs (M2.5, 2.2 pilot)
    for (px, py) in PI_HOLES:
        x, y = pi_xy(px, py)
        part += cyl(x, y, FLOOR - 0.01, PCB_Z, 3.0)
        part -= cyl(x, y, PCB_Z - 5.5, PCB_Z + 1, 1.1)
    # Feather: two screw standoffs at the USB-C end, two locating pegs at the USB-A end
    for (fx, fy) in F_SCREWS:
        x, y = f_xy(fx, fy)
        part += cyl(x, y, FLOOR - 0.01, PCB_Z, 2.5)
        part -= cyl(x, y, PCB_Z - 5.5, PCB_Z + 1, 1.1)
    for (fx, fy) in F_PEGS:
        x, y = f_xy(fx, fy)
        part += cyl(x, y, FLOOR - 0.01, PCB_Z, 2.5)
        part += cyl(x, y, PCB_Z - 0.01, PCB_Z + 1.0, 1.05) + cyl(x, y, PCB_Z + 1.0, PCB_Z + 1.3, 1.05, 0.75)

    # lid screws: through holes with countersinks from underneath (M3 countersunk)
    for (x, y) in BOSSES:
        part -= cyl(x, y, -1, 20, 1.7)
        part -= cyl(x, y, -0.01, 1.65, 3.35, 1.7)
    # rubber feet pockets
    for (x, y) in FEET:
        part -= cyl(x, y, -1, 0.6, 5.25)
    # underside badge, mirrored so it reads correctly from below
    part -= extrude(text_cs('XBRIDGE', 6.0, (W / 2, D / 2 + 6), mirror=True), -1, 0.6)
    part -= extrude(text_cs('MK1', 4.0, (W / 2, D / 2 - 6), mirror=True), -1, 0.6)
    return part


# ----------------------------------------------------------------- small parts (assembled position)
def lens():
    lx, ly = LENS_XY
    z_head0 = H - LENS_RECESS
    head = box(lx - LENS_HEAD / 2, lx + LENS_HEAD / 2, ly - LENS_HEAD / 2, ly + LENS_HEAD / 2, z_head0, z_head0 + 1.6)
    rod_bottom = PCB_Z + FEATHER_T + 2.6
    rod = cyl(lx, ly, rod_bottom, z_head0 + 0.01, 1.6) + cyl(lx, ly, rod_bottom - 0.01, rod_bottom + 0.4, 1.2, 1.6)
    return head + rod


def boot_key():
    bx, by = BOOT_XY
    z_head0 = H - KEY_RECESS + KEY_GAP
    head = M.batch_hull([
        box(bx - KEY_HEAD / 2, bx + KEY_HEAD / 2, by - KEY_HEAD / 2, by + KEY_HEAD / 2, z_head0, z_head0 + 1.6),
        box(bx - KEY_HEAD / 2 + 0.6, bx + KEY_HEAD / 2 - 0.6, by - KEY_HEAD / 2 + 0.6, by + KEY_HEAD / 2 - 0.6,
            z_head0, z_head0 + 2.2),
    ])
    shaft = cyl(bx, by, BOOT_TOP, z_head0 + 0.01, 1.3)
    return head + shaft


# ----------------------------------------------------------------- board stand-ins (for checks / renders)
def pi_model():
    def pbox(px0, px1, py0, py1, z0, z1):
        xa, ya = pi_xy(px0, py1)
        xb, yb = pi_xy(px1, py0)
        return box(min(xa, xb), max(xa, xb), min(ya, yb), max(ya, yb), z0, z1)
    pcb = extrude(rrect(PI_X0, PI_Y0, PI_X0 + PI_W, PI_Y0 + PI_L, 3.0), PCB_Z, PI_TOP)
    for (px, py) in PI_HOLES:
        x, y = pi_xy(px, py)
        pcb -= cyl(x, y, PCB_Z - 1, PI_TOP + 1, 1.375)
    t = PI_TOP
    parts = {
        'pcb': pcb,
        'ethernet': pbox(65.7, 87.0, 2.5, 18.1, t, t + 13.5),
        'usb_low': pbox(69.3, 87.0, 22.4, 35.5, t, t + 16.0),
        'usb_high': pbox(69.3, 87.0, 40.5, 53.6, t, t + 16.0),
        'power': pbox(6.7, 14.5, -1.2, 4.5, t, t + 3.0),
        'hdmi': pbox(24.8, 39.3, -1.3, 10.2, t, t + 6.5),
        # square body behind the board edge, round 6 mm collar sticking out 2.4 mm
        'audio': pbox(50.3, 57.0, 0.0, 12.0, t, t + 6.0)
                 + cyl(0, 0, 0, 2.4, 3.0).rotate([0, 90, 0]).translate([pi_xy(53.5, 0)[0], pi_xy(53.5, 0)[1], t + 3.0]),
        'gpio': pbox(7.1, 57.9, 51.0, 56.0, t, t + 8.5),
        # female jumper housings on pins 14/16/18 + room for the wires to bend
        'jumpers': pbox(22.3, 30.0, 52.5, 55.1, t, t + 16.5 + 4.0),
        'soc': pbox(20.0, 34.0, 24.7, 38.6, t, t + 1.4),
        'sd_card': pbox(-2.5, 12.0, 22.0, 34.0, PCB_Z - 1.5, PCB_Z),
        'underside': pbox(2.0, 84.0, 2.0, 54.0, PCB_Z - 1.6, PCB_Z) - pbox(0, 9, 0, 9, 0, 40)
                     - pbox(56, 67, 0, 9, 0, 40) - pbox(0, 9, 47, 56, 0, 40) - pbox(56, 67, 47, 56, 0, 40),
    }
    return parts


def feather_mesh():
    m = trimesh.load(FEATHER_STL)
    # board (x, y, z) -> case: X = FX0 + y, Y = FY0 - x, Z = PCB_Z + z
    T = np.array([[0, 1, 0, FX0], [-1, 0, 0, FY0], [0, 0, 1, PCB_Z], [0, 0, 0, 1]], float)
    m.apply_transform(T)
    return m


def dongle():
    y_face = FY0 - USB_A_FACE_FX
    z0 = USB_A_Z - DONGLE_H / 2
    return box(DONGLE_X - DONGLE_W / 2, DONGLE_X + DONGLE_W / 2, y_face - DONGLE_BODY, y_face, z0, z0 + DONGLE_H)


# ----------------------------------------------------------------- output
def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False)


def print_pose(name, m):
    """Rotate/translate each part into its printing orientation, on the bed at z = 0."""
    if name == 'lid':
        m = m.rotate([180, 0, 0])
    elif name in ('lens', 'boot_key'):
        m = m.rotate([180, 0, 0])
    (x0, y0, z0), (x1, y1, z1) = m.bounding_box()[:3], m.bounding_box()[3:]
    return m.translate([-(x0 + x1) / 2, -(y0 + y1) / 2, -z0])


def build():
    return {'lid': lid(), 'base': base(), 'lens': lens(), 'boot_key': boot_key()}


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'out')
    os.makedirs(out, exist_ok=True)
    parts = build()
    for name, m in parts.items():
        print(name, 'status', m.status(), 'volume %.1f cm3' % (m.volume() / 1000), 'genus', m.genus())
        to_trimesh(print_pose(name, m)).export(os.path.join(out, 'xbridge_%s.stl' % name))
        to_trimesh(m).export(os.path.join(out, 'assembled_%s.stl' % name))
    print('case outside: %.1f x %.1f x %.1f mm' % (W, D, H))
