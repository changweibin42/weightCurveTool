# -*- coding: utf-8 -*-
"""Pure topology/range helpers extracted verbatim from V17.5.1."""
import numpy as np
from ..Logger import log

def _projection_ownership_mask(points,positions,segments,target_indices):
    """Nearest skeletal-part competition, independent of current skin weights.

    Segments belong to their parent joint (the bone it drives). Joint sites
    disambiguate shared endpoints. Every other influence competes, locked or
    unlocked. This geometric estimate is not an anatomical guarantee.
    """
    points=np.asarray(points,dtype=float);positions=np.asarray(positions,dtype=float)
    if (points.ndim!=2 or points.shape[1]!=3 or positions.ndim!=2
            or positions.shape[1]!=3 or not np.all(np.isfinite(points))
            or not np.all(np.isfinite(positions))):
        raise RuntimeError('Invalid mesh or skeleton positions for range judgment')
    target=set(target_indices);n=len(points)
    nearest=[np.full(n,np.inf),np.full(n,np.inf)]
    sites=[np.full(n,np.inf),np.full(n,np.inf)]
    for i,p in enumerate(positions):
        side=0 if i in target else 1
        d=np.sum((points-p)**2,axis=1)
        sites[side]=np.minimum(sites[side],d)
        nearest[side]=np.minimum(nearest[side],d)
    for owner,a,b in segments:
        vector=positions[b]-positions[a];den=float(np.dot(vector,vector))
        if den<1e-16:continue
        t=np.clip((points-positions[a])@vector/den,0.,1.)
        d=np.sum((points-(positions[a]+t[:,None]*vector))**2,axis=1)
        side=0 if owner in target else 1
        nearest[side]=np.minimum(nearest[side],d)
    extent=max(float(np.ptp(positions,axis=0).max()),1e-6)
    tol=(extent*1e-8)**2
    clear=nearest[0]+tol<nearest[1]
    tie=(np.abs(nearest[0]-nearest[1])<=tol)&(sites[0]+tol<sites[1])
    return clear|tie



def _projection_connected_band(graph,points,axis,centre,half_width):
    """Flood only INSIDE the projection slab; extend only through its FWD cap.

    Full-mesh connectivity is deliberately not used to pick the band: an arm
    and torso are globally connected through the shoulder. No long-range
    Euclidean-neighbour edges are introduced.
    """
    points=np.asarray(points,dtype=float);axis=np.asarray(axis,dtype=float)
    length=float(np.linalg.norm(axis))
    if (length<1e-10 or not np.isfinite(length) or half_width<=0
            or not np.isfinite(half_width) or len(graph)!=len(points)):
        raise RuntimeError('Invalid projection band inputs')
    axis=axis/length;centre=np.asarray(centre,dtype=float)
    signed=(points-centre)@axis
    eps=max(half_width,1.)*1e-9
    allowed=np.abs(signed)<=half_width+eps
    candidates=np.flatnonzero(allowed)
    if not len(candidates):
        raise RuntimeError('投影带内没有顶点；请增大半径或检查关节位置')
    seed=int(candidates[np.argmin(np.sum((points[candidates]-centre)**2,axis=1))])
    band=np.zeros(len(points),dtype=bool);band[seed]=True;queue=[seed]
    while queue:
        v=queue.pop()
        for nb in graph[v]:
            if allowed[nb] and not band[nb]:band[nb]=True;queue.append(nb)
    # Separate flood: it may not walk back through discarded band components
    # or through the BWD halfspace to reach a different body part.
    forward=signed>half_width+eps
    outer=np.zeros(len(points),dtype=bool);queue=[]
    for v in np.flatnonzero(band):
        for nb in graph[v]:
            if forward[nb] and not outer[nb]:outer[nb]=True;queue.append(nb)
    while queue:
        v=queue.pop()
        for nb in graph[v]:
            if forward[nb] and not outer[nb]:outer[nb]=True;queue.append(nb)
    return band,outer,signed,seed,int(allowed.sum()-band.sum())



def _projection_plane_rings(points,polygons,axis,centre):
    """Surface/plane contours, independent of falloff slab width.

    Crossings connect through original faces, including a narrow slab with
    no original vertices. A tiny deterministic plane offset avoids ambiguous
    vertex/edge-on-plane degeneracies. An open clothing section is accepted
    only as one unbranched path ending at actual mesh boundary edges. Its
    transverse hull must still enclose the axis to become a body anchor.
    """
    points=np.asarray(points,dtype=float);axis=np.asarray(axis,dtype=float)
    axis=axis/np.linalg.norm(axis);centre=np.asarray(centre,dtype=float)
    scale=max(float(np.ptp(points,axis=0).max()),1.)
    d=(points-centre)@axis-scale*1e-8
    links={};crossings={};edge_counts={}
    for polygon in polygons:
        hits=[]
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            edge=tuple(sorted((int(a),int(b))))
            edge_counts[edge]=edge_counts.get(edge,0)+1
            if (d[a]<0.)==(d[b]<0.):continue
            if edge not in crossings:
                t=d[a]/(d[a]-d[b]);crossings[edge]=points[a]+t*(points[b]-points[a])
            hits.append(edge)
        if len(hits)!=2:continue
        a,b=hits
        links.setdefault(a,set()).add(b);links.setdefault(b,set()).add(a)
    remaining=set(links);result=[]
    while remaining:
        seed=min(remaining);remaining.remove(seed);queue=[seed];group=set()
        while queue:
            v=queue.pop();group.add(v)
            for nb in links[v]:
                if nb in remaining:remaining.remove(nb);queue.append(nb)
        if len(group)<3:continue
        ends=[v for v in group if len(links[v])==1]
        closed=all(len(links[v])==2 for v in group)
        open_section=(len(ends)==2 and all(len(links[v]) in (1,2) for v in group)
                      and all(edge_counts[v]==1 for v in ends))
        if not (closed or open_section):continue
        edges=sorted(group);samples=np.array([crossings[e] for e in edges])
        result.append(dict(closed=closed,vertices=set(v for e in edges for v in e),
            encloses=_projection_axis_enclosed(samples,range(len(samples)),axis,centre)))
    return result



def _projection_surface_distances(graph,points,seeds,domain=None):
    import heapq
    points=np.asarray(points);result=np.full(len(points),np.inf);queue=[]
    for v in seeds:
        if domain is None or domain[v]:
            result[v]=0.;heapq.heappush(queue,(0.,v))
    while queue:
        value,v=heapq.heappop(queue)
        if value!=result[v]:continue
        for nb in graph[v]:
            if domain is not None and not domain[nb]:continue
            update=value+float(np.linalg.norm(points[v]-points[nb]))
            if update<result[nb]:result[nb]=update;heapq.heappush(queue,(update,nb))
    return result



def _projection_ik_ring_gate(graph,points,axis,centre,half_width,polygons,band,outer):
    """IK keeps its slab/FWD domain; thin rings only prevent branch leakage."""
    rings=_projection_plane_rings(points,polygons,axis,centre)
    enclosing=[r for r in rings if r['encloses']]
    if len(enclosing)!=1:return band,outer,False
    body=enclosing[0]['vertices']
    other=set().union(*(r['vertices'] for r in rings if not r['encloses']))
    if not other or body&other:return band,outer,False
    candidate=band|outer
    # Preserve the established answer when no competing ring was reached.
    if not any(candidate[v] for v in other):return band,outer,False
    axis=np.asarray(axis,dtype=float);axis=axis/np.linalg.norm(axis)
    signed=(np.asarray(points)-np.asarray(centre))@axis
    eps=max(half_width,1.)*1e-9;domain=signed>=-half_width-eps
    targets=[v for v in body if band[v]]
    rivals=[v for v in other if domain[v]]
    if not targets or not rivals:return band,outer,False
    a=_projection_surface_distances(graph,points,targets,domain)
    b=_projection_surface_distances(graph,points,rivals,domain)
    keep=np.isfinite(a)&(a+eps<b)
    allowed=band&keep
    # Reachability is still inside the requested slab, followed only through
    # the FWD cap. It never turns all parent support into an allocation mask.
    seed=min(targets,key=lambda v:float(np.sum((points[v]-centre)**2)))
    clean=np.zeros(len(points),dtype=bool);clean[seed]=True;queue=[seed]
    while queue:
        v=queue.pop()
        for nb in graph[v]:
            if allowed[nb] and not clean[nb]:clean[nb]=True;queue.append(nb)
    forward=(signed>half_width+eps)&keep;extension=np.zeros(len(points),dtype=bool);queue=[]
    for v in np.flatnonzero(clean):
        for nb in graph[v]:
            if forward[nb] and not extension[nb]:extension[nb]=True;queue.append(nb)
    while queue:
        v=queue.pop()
        for nb in graph[v]:
            if forward[nb] and not extension[nb]:extension[nb]=True;queue.append(nb)
    return clean,extension,True



def _projection_axis_enclosed(points,vertices,axis,centre):
    """Does the transverse hull of this surface component enclose the axis?

    Unlike nearest-point tests this identifies a wide torso ring even when a
    sleeve is closer to an internal joint. Ambiguous components are rejected.
    """
    axis=np.asarray(axis,dtype=float);axis=axis/np.linalg.norm(axis)
    reference=np.eye(3)[int(np.argmin(np.abs(axis)))]
    u=np.cross(axis,reference);u=u/np.linalg.norm(u);v=np.cross(axis,u)
    p=np.asarray(points)[list(vertices)]-np.asarray(centre)
    xy=sorted(set(map(tuple,np.column_stack((p@u,p@v)).tolist())))
    if len(xy)<3:return False
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    extent=max(max(abs(x) for row in xy for x in row),1e-6);tol=extent*extent*1e-10
    lower=[]
    for q in xy:
        while len(lower)>=2 and cross(lower[-2],lower[-1],q)<=tol:lower.pop()
        lower.append(q)
    upper=[]
    for q in reversed(xy):
        while len(upper)>=2 and cross(upper[-2],upper[-1],q)<=tol:upper.pop()
        upper.append(q)
    hull=lower[:-1]+upper[:-1]
    return len(hull)>=3 and all(cross(a,b,(0.,0.))>tol for a,b in zip(hull,hull[1:]+hull[:1]))



def _projection_boundary_loops(polygons):
    """Return only unambiguous closed mesh-boundary components."""
    counts={}
    for polygon in polygons:
        polygon=list(map(int,polygon))
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            if a==b:continue
            edge=tuple(sorted((a,b)));counts[edge]=counts.get(edge,0)+1
    links={}
    for (a,b),count in counts.items():
        if count==1:links.setdefault(a,set()).add(b);links.setdefault(b,set()).add(a)
    remaining=set(links);loops=[]
    while remaining:
        seed=min(remaining);remaining.remove(seed);queue=[seed];group=set()
        while queue:
            v=queue.pop();group.add(v)
            for nb in links[v]:
                if nb in remaining:remaining.remove(nb);queue.append(nb)
        if len(group)>=3 and all(len(links[v])==2 for v in group):loops.append(group)
    return loops



def _projection_distal_seeds(graph,body,other,boundary_loops):
    """Use actual distal cuffs instead of pinning entire sleeve slabs outside.

    Body-touching boundary loops (hem/front openings) are protected. If a
    separated part has no safe cuff, retain its old conservative anchors.
    """
    loops=[set(loop) for loop in (boundary_loops or []) if not (set(loop)&body)]
    remaining=set(other);chosen=set();cuff_parts=0;fallback_parts=0
    while remaining:
        seed=min(remaining);remaining.remove(seed);queue=[seed];group=set()
        while queue:
            v=queue.pop();group.add(v)
            for nb in graph[v]:
                if nb in remaining:remaining.remove(nb);queue.append(nb)
        cuffs=[loop for loop in loops if loop<=group]
        if cuffs:
            chosen.update(set().union(*cuffs));cuff_parts+=1
        else:
            chosen.update(group);fallback_parts+=1
    return chosen,cuff_parts,fallback_parts



def _projection_surface_partition(graph,points,frames,half_width,boundary_loops=None,polygons=None):
    """Surface-geodesic competition from complete, separated ring anchors.

    Only split bands with exactly one axis-enclosing component provide
    anchors. Merged upper-body/arm components never become torso anchors.
    No off-chain joint positions or existing weights determine membership.
    """
    import heapq
    points=np.asarray(points,dtype=float);body=set();other=set();used=[]
    for frame_index,(axis,centre) in enumerate(frames):
        axis=np.asarray(axis,dtype=float);axis=axis/np.linalg.norm(axis)
        d=(points-np.asarray(centre))@axis
        remaining=set(map(int,np.flatnonzero(np.abs(d)<=half_width+max(half_width,1.)*1e-9)))
        components=[]
        while remaining:
            seed=min(remaining);remaining.remove(seed);queue=[seed];group=set()
            while queue:
                v=queue.pop();group.add(v)
                for nb in graph[v]:
                    if nb in remaining:remaining.remove(nb);queue.append(nb)
            components.append(group)
        if len(components)<2:continue
        enclosing=[i for i,c in enumerate(components)
                   if _projection_axis_enclosed(points,c,axis,centre)]
        if len(enclosing)!=1:continue
        chosen=enclosing[0];body.update(components[chosen])
        for i,c in enumerate(components):
            if i!=chosen:other.update(c)
        used.append(frame_index)
    anchor_source='slab'
    if (not body or not other) and polygons:
        # Falloff radius does not decide whether a clean section exists.
        # Keep all successful legacy anchors/results; use face crossings only
        # when the wide or vertex-empty slab supplies no reliable anchors.
        ring_body=set();ring_other=set();ring_used=[]
        for frame_index,(axis,centre) in enumerate(frames):
            rings=_projection_plane_rings(points,polygons,axis,centre)
            enclosing=[r for r in rings if r['encloses']]
            if len(enclosing)!=1:continue
            ring_body.update(enclosing[0]['vertices']);ring_used.append(frame_index)
            for ring in rings:
                if not ring['encloses']:ring_other.update(ring['vertices'])
        if ring_body:
            # Keep sleeve roots available for the established axilla blend:
            # use genuine off-axis distal cuffs whenever they are reachable
            # from separated contours without crossing torso anchors.
            reachable=set(ring_other);queue=list(ring_other)
            while queue:
                v=queue.pop()
                for nb in graph[v]:
                    if nb not in ring_body and nb not in reachable:
                        reachable.add(nb);queue.append(nb)
            cuffs=set()
            for loop in boundary_loops or []:
                loop=set(loop)
                if loop&ring_body:continue
                if any(_projection_axis_enclosed(points,loop,axis,centre) for axis,centre in frames):continue
                if not ring_other or loop&reachable:cuffs.update(loop)
            if cuffs:ring_other=cuffs
        if ring_body and ring_other and not ring_body&ring_other:
            body,other,used=ring_body,ring_other,ring_used;anchor_source='face_plane'
            log.info('[Spine Rings V17.5.1] frames={}, body={}, other={}'.format(used,len(body),len(other)))
    if not body or not other:
        raise RuntimeError('脊椎自动范围缺少可分离的躯干环带；请使用当前顶点/软选范围')
    if body&other:
        raise RuntimeError('脊椎各投影带的区域标记冲突；请使用当前顶点/软选范围')
    candidate_other=set(other)
    other,cuff_parts,fallback_parts=_projection_distal_seeds(graph,body,other,boundary_loops)
    lengths=[{} for _ in graph];edge_lengths=[]
    for a in range(len(graph)):
        for b in graph[a]:
            if b>a:
                length=float(np.linalg.norm(points[a]-points[b]))
                if length<1e-12:raise RuntimeError('Mesh contains zero-length surface edges')
                lengths[a][b]=lengths[b][a]=length;edge_lengths.append(length)
    def distances(seeds):
        result=np.full(len(points),np.inf);queue=[]
        for v in seeds:result[v]=0.;heapq.heappush(queue,(0.,v))
        while queue:
            value,v=heapq.heappop(queue)
            if value!=result[v]:continue
            for nb,length in lengths[v].items():
                update=value+length
                if update<result[nb]:result[nb]=update;heapq.heappush(queue,(update,nb))
        return result
    bd=distances(body);od=distances(other)
    eps=max(float(np.median(edge_lengths)) if edge_lengths else 1.,1e-8)*1e-9
    mask=bd+eps<od
    return mask,dict(body_seeds=sorted(body),other_seeds=sorted(other),frames=used,
                     body_distance=bd,other_distance=od,candidate_other_seeds=sorted(candidate_other),
                     cuff_parts=cuff_parts,fallback_parts=fallback_parts,anchor_source=anchor_source)



def _projection_complete_support_tails(graph,mask,budget,protected):
    """Finish whole tapering residual-support components at a geometric edge.

    Never invent budget, cross a zero-budget gap, touch protected cuffs, or
    follow a weight increase away from the accepted area. A component is
    added only if ALL of it is reachable downhill from accepted neighbours.
    """
    mask=np.asarray(mask,dtype=bool).copy();budget=np.asarray(budget,dtype=float)
    if len(graph)!=len(mask) or budget.shape!=mask.shape or not np.all(np.isfinite(budget)):
        raise RuntimeError('Invalid chain support-tail inputs')
    positive=budget>1e-12;protected=set(protected)
    remaining=set(map(int,np.flatnonzero(positive&~mask)));recovered=[];groups=0;skipped=0
    while remaining:
        seed=min(remaining);remaining.remove(seed);component={seed};queue=[seed]
        while queue:
            v=queue.pop()
            for nb in graph[v]:
                if nb in remaining:remaining.remove(nb);component.add(nb);queue.append(nb)
        if any(v in protected or any(nb in protected for nb in graph[v]) for v in component):
            skipped+=1;continue
        reached=set();queue=[]
        for v in component:
            if any(mask[nb] and positive[nb] and budget[v]<=budget[nb]+1e-12 for nb in graph[v]):
                reached.add(v);queue.append(v)
        while queue:
            v=queue.pop()
            for nb in graph[v]:
                if nb in component and nb not in reached and budget[nb]<=budget[v]+1e-12:
                    reached.add(nb);queue.append(nb)
        if reached==component:
            recovered.extend(component);groups+=1
        else:skipped+=1
    if recovered:mask[recovered]=True
    return mask,dict(vertices=sorted(recovered),components=groups,skipped_components=skipped)



def _projection_ik_assignment_plan(pair,apply_mask,fraction=None):
    """In-memory pick->parent reset, then apply the new range atomically."""
    pair=np.asarray(pair,dtype=float);cap=pair.sum(axis=1)
    apply=np.asarray(apply_mask,dtype=bool)&(cap>1e-12)
    reset=(~apply)&(pair[:,0]!=0.)
    write=apply|reset
    values=np.column_stack((np.zeros(len(cap)),cap))
    if fraction is not None:
        fraction=np.clip(np.asarray(fraction,dtype=float),0.,1.)
        values[apply,0]=cap[apply]*fraction[apply]
        values[apply,1]=cap[apply]-values[apply,0]
    return dict(apply_mask=apply,reset_mask=reset,write_mask=write,values=values)



def _projection_spine_connected_influences(graph,values,mask,anchors):
    """Drop unanchored per-influence islands, keeping the group's total.

    All columns are classified from the ORIGINAL computed matrix together.
    Removed mass is distributed only among surviving computed columns, so no
    joint name or fixed recipient is used. No reliable anchor => no cleanup.
    """
    original=np.asarray(values,dtype=float);result=original.copy()
    eligible=np.asarray(mask,dtype=bool)&(original.sum(axis=1)>1e-12)
    anchors=set(anchors or [])&set(map(int,np.flatnonzero(eligible)))
    if not anchors:return result,dict(rows=[],removed_components=[],skipped_rows=[])
    remove=np.zeros(original.shape,dtype=bool);components=[]
    for column in range(original.shape[1]):
        remaining=set(map(int,np.flatnonzero(eligible&(original[:,column]>1e-12))))
        groups=[];has_anchored=False
        while remaining:
            seed=min(remaining);remaining.remove(seed);queue=[seed];group=set()
            while queue:
                v=queue.pop();group.add(v)
                for nb in graph[v]:
                    if nb in remaining:remaining.remove(nb);queue.append(nb)
            groups.append(group)
            if group&anchors:has_anchored=True
        if not has_anchored:continue
        for group in groups:
            if not (group&anchors):
                ids=sorted(group);remove[ids,column]=True
                components.append(dict(column=column,vertices=ids))
    touched=np.any(remove,axis=1);result[remove]=0.
    cap=original.sum(axis=1);remaining=result.sum(axis=1)
    valid=touched&(remaining>1e-12)
    result[valid]*=(cap[valid]/remaining[valid])[:,None]
    skipped=touched&~valid;result[skipped]=original[skipped]
    return result,dict(rows=np.flatnonzero(valid).tolist(),removed_components=components,
                       skipped_rows=np.flatnonzero(skipped).tolist())

