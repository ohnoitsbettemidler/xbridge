import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xbridge_case as xc
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
# board stand-ins for the renders
pi = xc.pi_model()
import manifold3d
allpi = manifold3d.Manifold.batch_boolean(list(pi.values()), manifold3d.OpType.Add)
xc.to_trimesh(allpi).export(os.path.join(out, 'pi.stl'))
xc.feather_mesh().export(os.path.join(out, 'feather.stl'))
xc.to_trimesh(xc.dongle()).export(os.path.join(out, 'dongle.stl'))
