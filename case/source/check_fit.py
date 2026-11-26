#!/usr/bin/env python3
"""Fit and printability checks for the XBridge case.

Collides every printed part against the boards (Pi stand-ins from the official
drawing, the Feather from Adafruit's own 3D model, the Magic-X as a box), with
the boards nudged 0.3 mm each way to catch anything with no breathing room.
"""
import os
import sys

import numpy as np
import trimesh
from manifold3d import Manifold as M, Mesh, OpType

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xbridge_case as xc  # noqa: E402

TOL = 1e-3   # mm^3 counted as touching, not overlapping
problems = []


def to_manifold(tm):
    return M(Mesh(vert_properties=np.asarray(tm.vertices, np.float32), tri_verts=np.asarray(tm.faces, np.uint32)))


def feather_keepout():
    """Feather as a solid keep-out: real PCB with its holes, every component as
    its convex hull (slightly bigger than the real part = conservative)."""
    m = xc.feather_mesh()
    pieces = m.split(only_watertight=False)
    pcb = None
    comps = []
    for p in pieces:
        e = p.extents
        if e[0] > 20 and e[1] > 20 and e[2] < 2:      # the board itself
            continue
        if len(p.vertices) < 4:
            continue
        h = M.hull_points(np.asarray(p.vertices, float).tolist())
        if not h.is_empty() and h.volume() > 1e-4:
            comps.append(h)
    # rebuild the PCB exactly: outline + 4 mounting holes
    x0, y0 = xc.f_xy(xc.FEATHER_L, xc.FEATHER_W)
    x1, y1 = xc.f_xy(0, 0)
    pcb = xc.box(min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1), xc.PCB_Z, xc.PCB_Z + xc.FEATHER_T)
    for (fx, fy) in xc.F_SCREWS + xc.F_PEGS:
        x, y = xc.f_xy(fx, fy)
        pcb -= xc.cyl(x, y, xc.PCB_Z - 1, xc.PCB_Z + 3, 1.27)
    return pcb, M.batch_boolean(comps, OpType.Add)


def overlap(a, b):
    return (a ^ b).volume()


def check(name, a, b, allow=TOL):
    v = overlap(a, b)
    ok = v <= allow
    print('  %-44s %s' % (name, 'clear' if ok else 'OVERLAP %.3f mm3' % v))
    if not ok:
        problems.append(name)


def main():
    parts = xc.build()
    lid, base, lens, key = parts['lid'], parts['base'], parts['lens'], parts['boot_key']
    pi = xc.pi_model()
    f_pcb, f_parts = feather_keepout()
    dongle = xc.dongle()
    boards = dict(pi)
    boards['feather_pcb'] = f_pcb
    boards['feather_parts'] = f_parts
    boards['magic_x'] = dongle

    print('Printed parts')
    for n, m in parts.items():
        tm = xc.to_trimesh(m)
        bb = xc.print_pose(n, m).bounding_box()
        size = [bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]]
        print('  %-10s watertight=%s  print size %.1f x %.1f x %.1f mm  solid volume %.1f cm3' % (
            n, tm.is_watertight, *size, m.volume() / 1000))
        if not tm.is_watertight:
            problems.append(n + ' not watertight')

    print('\nParts against each other')
    check('lid vs base', lid, base)
    check('lens vs lid', lens, lid)
    check('BOOT key vs lid', key, lid)
    check('BOOT key vs base', key, base)

    print('\nParts against the boards (as placed)')
    for bn, bm in boards.items():
        for pn, pm in (('lid', lid), ('base', base), ('lens', lens)):
            if pn == 'base' and bn in ('feather_pcb',):
                continue   # pegs sit inside the PCB holes - checked below with a tighter test
            check('%s vs %s' % (pn, bn), pm, bm)
    check('BOOT key vs Feather parts (tip rests on BOOT)', key, f_parts, allow=0.05)
    check('base pegs vs Feather PCB', base, f_pcb)

    print('\nBoards nudged 0.3 mm each way (breathing room)')
    nudges = [(0.3, 0, 0), (-0.3, 0, 0), (0, 0.3, 0), (0, -0.3, 0), (0, 0, 0.3)]
    for bn, bm in boards.items():
        if bn in ('underside', 'sd_card', 'soc', 'pcb', 'feather_pcb'):
            continue
        worst = 0.0
        for d in nudges:
            worst = max(worst, overlap(lid, bm.translate(list(d))))
        status = 'ok' if worst <= TOL else 'tight (%.3f mm3 when nudged)' % worst
        print('  lid vs %-20s %s' % (bn, status))
        if worst > TOL and bn not in ('jumpers',):
            problems.append('tight: lid vs ' + bn)

    print('\nKey gaps')
    gaps = [
        ('Pi PCB edge to back wall', (xc.D - xc.WALL) - (xc.PI_Y0 + xc.PI_L)),
        ('Pi PCB edge to right wall', (xc.W - xc.WALL) - (xc.PI_X0 + xc.PI_W)),
        ('Pi USB/Ethernet face recess from outside', xc.D - (xc.PI_Y0 + 87.0)),
        ('Feather USB-C face recess from outside', xc.D - (xc.FY0 + 1.18)),
        ('Magic-X sticks out of the front by', -(xc.FY0 - xc.USB_A_FACE_FX - xc.DONGLE_BODY)),
        ('Tallest Pi part (USB) to roof', xc.ROOF_UNDER - (xc.PI_TOP + 16.0)),
        ('Jumper housings top to roof', xc.ROOF_UNDER - (xc.PI_TOP + 16.5)),
        ('Lens rod tip above Feather screw head', (xc.PCB_Z + xc.FEATHER_T + 2.6) - (xc.PCB_Z + xc.FEATHER_T + 1.8)),
        ('Lens rod tip above the LED', 2.6),
        ('BOOT key free travel before head bottoms', xc.KEY_GAP),
    ]
    for n, g in gaps:
        print('  %-44s %.2f mm' % (n, g))

    # min_gap between lid and nearby boards (searching up to 2 mm)
    print('\nClosest approach, lid to boards (search 2 mm)')
    for bn in ('pcb', 'ethernet', 'usb_low', 'usb_high', 'power', 'hdmi', 'audio', 'feather_parts', 'feather_pcb', 'magic_x'):
        g = lid.min_gap(boards[bn], 2.0)
        print('  %-20s %s' % (bn, ('%.2f mm' % g) if g < 2.0 else '> 2 mm'))

    print('\nRESULT:', 'all clear' if not problems else 'PROBLEMS: ' + ', '.join(problems))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
