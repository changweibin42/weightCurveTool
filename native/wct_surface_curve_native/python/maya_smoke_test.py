# -*- coding: utf-8 -*-
"""Run in Maya's Python tab using exec(compile(open(...).read(),..., 'exec')).
Creates a separate test sphere and live curve. Does not modify skin weights.
Set PACKAGE to the extracted source package folder before running.
"""
from pathlib import Path
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om

PACKAGE = Path(r'D:\wct_surface_curve_native')
PLUGIN = PACKAGE / 'dist' / 'wctSurfaceCurve.mll'
if not PLUGIN.is_file():
    raise RuntimeError('Build the plugin first: {}'.format(PLUGIN))
cmds.loadPlugin(str(PLUGIN), quiet=True)

def obj(name):
    sel=om.MSelectionList();sel.add(name);return sel.getDependNode(0)

def mesh_data(points,faces):
    data=om.MFnMeshData().create()
    om.MFnMesh().create(om.MPointArray(points.tolist()),[3]*len(faces),faces.reshape(-1).tolist(),parent=data)
    return data

root=cmds.createNode('transform',name='WCT_NATIVE_TEST#')
mesh=cmds.polySphere(radius=5,subdivisionsX=32,subdivisionsY=16,name='wctNativeSphere#',constructionHistory=False)[0]
mesh=cmds.parent(mesh,root)[0]
cmds.polyTriangulate(mesh,constructionHistory=False)
sel=om.MSelectionList();sel.add(mesh);path=sel.getDagPath(0);path.extendToShape()
fn=om.MFnMesh(path)
points=np.array([[p.x,p.y,p.z] for p in fn.getPoints(om.MSpace.kWorld)])
counts,connects=fn.getVertices();faces=np.asarray(connects,dtype=int).reshape(-1,3)
node=cmds.createNode('wctSurfaceCurve',name='wctNativeTestCompute#')
# Track non-DAG test node for explicit cleanup without global searches.
cmds.addAttr(root,longName='testCompute',attributeType='message')
cmds.connectAttr(node+'.message',root+'.testCompute')
nodefn=om.MFnDependencyNode(obj(node))
nodefn.findPlug('referenceMesh',False).setMObject(mesh_data(points,faces))
nodefn.findPlug('displayMesh',False).setMObject(mesh_data(points,faces))
nodefn.findPlug('epIndices',False).setMObject(om.MFnIntArrayData().create([0,3,6,9]))
cmds.setAttr(node+'.surfaceOffset',0)
cmds.setAttr(node+'.epCount',12)
cmds.setAttr(node+'.closed',True)
controls=[]
for i,p in enumerate(([5,0,0],[0,0,5],[-5,0,0],[0,0,-5])):
    vertex=int(np.argmin(np.linalg.norm(points-np.asarray(p),axis=1)))
    loc=cmds.spaceLocator(name='wctNativeTestLoc#')[0];loc=cmds.parent(loc,root)[0]
    cmds.xform(loc,worldSpace=True,translation=points[vertex].tolist())
    controls.append((loc,vertex))
    for name,typ,value,target in [('wct_id0','long',vertex,'vertex0'),('wct_id1','long',vertex,'vertex1'),('wct_id2','long',vertex,'vertex2'),('wct_u','double',1.,'weight0'),('wct_v','double',0.,'weight1'),('wct_w','double',0.,'weight2')]:
        cmds.addAttr(loc,longName=name,attributeType=typ,defaultValue=value)
        cmds.connectAttr(loc+'.'+name,'{}.anchors[{}].{}'.format(node,i,target))
curve=cmds.createNode('transform',name='wctNativeTestCurve#',parent=root)
shape=cmds.createNode('nurbsCurve',parent=curve)
cmds.connectAttr(node+'.outputCurve',shape+'.create')
cmds.setAttr(shape+'.overrideEnabled',True);cmds.setAttr(shape+'.overrideColor',17);cmds.setAttr(shape+'.lineWidth',4)

cmds.dgdirty(node)
def read_local():
    values=cmds.getAttr(node+'.outputLocalPoints')
    if values is None or len(values)==0:raise RuntimeError('Empty outputLocalPoints: '+node)
    return np.asarray(values,dtype=float)[:,:3]

def read_curve():
    sel=om.MSelectionList();sel.add(shape);f=om.MFnNurbsCurve(sel.getDagPath(0))
    assert f.form==om.MFnNurbsCurve.kPeriodic
    return np.array([[p.x,p.y,p.z] for p in (f.getPointAtParam(float(i),om.MSpace.kWorld) for i in range(12))])

before=read_local();np.testing.assert_allclose(read_curve(),before,atol=3e-5,rtol=0)
# Evaluate outputs in both orders and verify that changing a connected binding dirties both.
loc,vertex=controls[0]
tri=next(row for row in faces if vertex in row)
other=int(next(v for v in tri if v!=vertex))
cmds.setAttr(loc+'.wct_id0',other)
after_curve=read_curve();after=read_local()
assert np.linalg.norm(after[0]-before[0])>1e-3,'Connected binding failed to update output'
np.testing.assert_allclose(after_curve,after,atol=3e-5,rtol=0)
cmds.setAttr(loc+'.wct_id0',vertex)
np.testing.assert_allclose(read_local(),before,atol=3e-5,rtol=0)
# A display-only pose change must not change reference-space EPs.
offset=np.array([3.,4.,5.])
nodefn.findPlug('displayMesh',False).setMObject(mesh_data(points+offset,faces))
np.testing.assert_allclose(read_curve(),before+offset,atol=3e-5,rtol=0)
np.testing.assert_allclose(read_local(),before,atol=3e-5,rtol=0)
nodefn.findPlug('displayMesh',False).setMObject(mesh_data(points,faces))
cmds.select(curve,replace=True);cmds.refresh(force=True)
print('[WCT native] PASS: ID=0x00142A04, periodic EP phase, DG binding updates, output order, display/reference mapping')
print('Test root:',root,'; test compute:',node)
print('This test uses binding attrs; moving a test locator transform alone does not change its surface binding.')
print('Cleanup these test nodes only: cmds.delete({!r}, {!r})'.format(root,node))
