# -*- coding: utf-8 -*-
"""Run once in Maya, then reproduce the failed loc drag.

Observes the next loop/path failure, saves numerical inputs, and uninstalls
itself. Original exceptions still propagate; no weight/scene writes added.
Re-running this file replaces the observer rather than stacking wrappers.
"""
import inspect
import json
import os
import sys
import tempfile
import traceback
import hashlib
from collections import deque
import maya.cmds as cmds
import maya.api.OpenMaya as om
from weightCurveTool.tools import StrictCurveRegion as S


def _point(point):
    return [float(point.x),float(point.y),float(point.z)]


def _loop_audit(paths):
    edges=set()
    for row in paths:
        route=row.get('path',[])
        edges.update(tuple(sorted((int(a),int(b)))) for a,b in zip(route,route[1:]))
    links={}
    for a,b in edges:
        links.setdefault(a,set()).add(b);links.setdefault(b,set()).add(a)
    before={str(a):len(bs) for a,bs in links.items()}
    leaves=deque(a for a,bs in links.items() if len(bs)<2);removed=[]
    while leaves:
        a=leaves.popleft()
        if a not in links:continue
        for b in list(links[a]):
            links[b].discard(a)
            if len(links[b])<2:leaves.append(b)
        del links[a];removed.append(a)
    remaining=set(links);components=[]
    while remaining:
        seed=min(remaining);remaining.remove(seed);queue=[seed];group=[]
        while queue:
            a=queue.pop();group.append(a)
            for b in links[a]:
                if b in remaining:remaining.remove(b);queue.append(b)
        components.append(sorted(group))
    bad=[dict(vertex=int(a),degree=len(bs),neighbors=sorted(bs),
              path_indices=[i for i,row in enumerate(paths) if a in row.get('path',[])])
         for a,bs in sorted(links.items()) if len(bs)!=2]
    reason=('path_failure' if any('error' in p for p in paths) else
            'empty_after_spur_removal' if not links else
            'branching_after_spur_removal' if bad else
            'multiple_loops' if len(components)>1 else 'simple_loop')
    return dict(reason=reason,union_edges=[list(e) for e in sorted(edges)],
                degrees_before=before,removed_spurs=removed,
                problem_vertices=bad,components=components)


class LoopFailureCapture:
    def __init__(self,module):
        self.module=module;self.loop=module._wct_loop;self.path=module._wct_path
        self.active=None;self.output=None;self.last_payload=None
        self.loop_wrapper=None;self.path_wrapper=None

    def install(self):
        def path_wrapper(a,b,graph,points,median):
            try:
                result=self.path(a,b,graph,points,median)
            except Exception as exc:
                if self.active is not None:
                    self.active['paths'].append(dict(a=int(a),b=int(b),error=str(exc)))
                raise
            if self.active is not None:
                self.active['paths'].append(dict(a=int(a),b=int(b),path=list(map(int,result))))
            return result

        def loop_wrapper(anchors,graph,points,median,label):
            previous=self.active
            self.active=dict(paths=[])
            try:
                return self.loop(anchors,graph,points,median,label)
            except RuntimeError as exc:
                if not any(fragment in str(exc) for fragment in (
                    'snapped boundary is not a simple loop',
                    'snapping produced multiple loops','Cannot connect boundary vertices')):
                    raise
                frame=inspect.currentframe().f_back
                try:
                    context=dict(frame.f_locals) if frame is not None else {}
                    payload=self.make_payload(anchors,graph,points,median,label,str(exc),context)
                    payload['traceback']=traceback.format_exc()
                    self.last_payload=payload
                    self.output=self.save(payload)
                    self.disarm()
                    print('[WCT LoopCapture] SAVED: '+self.output)
                    print('[WCT LoopCapture] reason={} problem_vertices={}; observer removed'.format(
                        payload['audit']['reason'],len(payload['audit']['problem_vertices'])))
                    print('[WCT LoopCapture] Upload this JSON. Original failure is still cancelled.')
                except Exception as capture_error:
                    print('[WCT LoopCapture] export failed; original failure preserved: '+str(capture_error))
                finally:
                    del frame
                raise
            finally:
                self.active=previous

        self.path_wrapper=path_wrapper;self.loop_wrapper=loop_wrapper
        self.module._wct_path=path_wrapper;self.module._wct_loop=loop_wrapper
        return self

    def disarm(self):
        if self.module._wct_loop is self.loop_wrapper:self.module._wct_loop=self.loop
        if self.module._wct_path is self.path_wrapper:self.module._wct_path=self.path

    def make_payload(self,anchors,graph,points,median,label,error,context):
        paths=list(self.active['paths'])
        payload=dict(format='wct_loop_failure_v1',label=str(label),error=error,
            python=sys.version,vertices=[_point(p) for p in points],median=float(median),
            anchors=list(map(int,anchors)),
            graph=[[[int(b),float(length)] for b,length in row.items()] for row in graph],
            paths=paths,audit=_loop_audit(paths))
        payload['surface_samples']={name:[_point(p)+[int(face),float(param)]
            for p,face,param in records] for name,records in context.get('curve_samples',{}).items()}
        # Numeric loop inputs above are sufficient for deterministic replay.
        # Add mesh/curve evidence when called by build_strict_region.
        try:
            mesh=context.get('mesh')
            if mesh is not None:
                payload['polygons']=[list(map(int,mesh.getPolygonVertices(i)))
                                     for i in range(int(mesh.numPolygons))]
            curves=[]
            for name in context.get('curve_names',()):
                fn=om.MFnNurbsCurve(self.module._wct_shape(name,'nurbsCurve'))
                curves.append(dict(name=name,uuid=cmds.ls(name,uuid=True),degree=int(fn.degree),
                    form=int(fn.form),knots=list(map(float,fn.knots())),
                    world_cvs=[_point(p)+[float(p.w)] for p in fn.cvPositions(om.MSpace.kWorld)]))
            payload['curves']=curves
        except Exception as exc:
            payload['scene_evidence_warning']=str(exc)
        source=getattr(self.module,'__file__',None)
        if source and os.path.isfile(source):
            with open(source,'rb') as stream:payload['region_source_sha256']=hashlib.sha256(stream.read()).hexdigest()
        return payload

    @staticmethod
    def save(payload):
        try:scene=cmds.file(q=True,sceneName=True)
        except Exception:scene=''
        locations=[os.path.dirname(scene),tempfile.gettempdir()]
        last_error=None
        for directory in locations:
            if not directory or not os.path.isdir(directory):continue
            path=None
            try:
                fd,path=tempfile.mkstemp(prefix='wct_loop_failure_',suffix='.json',dir=directory)
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    json.dump(payload,stream,ensure_ascii=False,allow_nan=False)
                return path
            except Exception as exc:
                last_error=exc
                if path:
                    try:os.remove(path)
                    except OSError:pass
        raise RuntimeError('Could not save loop case: '+str(last_error))


def arm_capture():
    old=getattr(S,'_wct_loop_capture',None)
    if old is not None:old.disarm()
    capture=LoopFailureCapture(S).install()
    S._wct_loop_capture=capture
    print('[WCT LoopCapture] ARMED: drag locs normally; next loop/path failure saves once.')
    print('[WCT LoopCapture] Run after opening the tool. Re-run if you restart/reload the tool.')
    print('[WCT LoopCapture] Manual stop: capture.disarm()')
    return capture


capture=arm_capture()
