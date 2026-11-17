# -*- coding: utf-8 -*-
"""Index-preserving boundary edits. No Maya API or weight solving here."""
import numpy as np


def deform_ordered_points(points, anchor_indices, old_anchors, new_anchors, closed):
    """Blend anchor displacements in inherited EP index, never side arclength.

    Base points/anchors are immutable references so repeated redraws cannot
    accumulate drift. A control point affects only its neighbouring intervals.
    """
    points=np.asarray(points,dtype=float)
    ids=np.asarray(anchor_indices,dtype=int)
    old=np.asarray(old_anchors,dtype=float);new=np.asarray(new_anchors,dtype=float)
    n=len(points)
    if (points.shape!=(n,3) or len(ids)<2 or old.shape!=(len(ids),3)
            or new.shape!=old.shape or len(set(ids.tolist()))!=len(ids)
            or np.any(ids<0) or np.any(ids>=n)
            or not all(np.all(np.isfinite(x)) for x in (points,old,new))):
        raise ValueError('Invalid boundary correspondence data')
    order=np.argsort(ids);ids=ids[order];delta=(new-old)[order]
    if closed:
        xp=np.r_[ids[-1]-n,ids,ids[0]+n]
        dp=np.vstack([delta[-1],delta,delta[0]])
    else:
        xp=ids;dp=delta
    move=np.column_stack([np.interp(np.arange(n),xp,dp[:,axis]) for axis in range(3)])
    result=points+move
    result[np.asarray(anchor_indices,dtype=int)]=new
    return result


def resample_anchor_intervals(dense, dense_anchors, output_anchors, count, closed):
    """Uniform local arc spacing; fixed source indices, seam and controls.

    Dense segments are in sorted source-anchor order. Never resample the
    complete ring: each control-to-control interval retains its own EP slots.
    """
    dense=np.asarray(dense,dtype=float)
    src=np.asarray(sorted(dense_anchors),dtype=int)
    ids=np.asarray(output_anchors,dtype=int)
    segments=len(ids) if closed else len(ids)-1
    if (dense.ndim!=2 or dense.shape[1]!=3 or not np.all(np.isfinite(dense))
            or len(src)!=segments+1 or len(ids)<2 or count<2
            or np.any(np.diff(src)<=0) or src[0]<0 or src[-1]>=len(dense)
            or np.any(np.diff(ids)<=0) or ids[0]<0 or ids[-1]>=count
            or (not closed and (ids[0]!=0 or ids[-1]!=count-1))):
        raise ValueError('Invalid anchored boundary sampling layout')
    out=np.zeros((count,3));assigned=np.zeros(count,bool)
    for i in range(segments):
        start=int(ids[i]);end=int(ids[(i+1)%len(ids)])
        steps=(end-start)%count if closed else end-start
        segment=dense[src[i]:src[i+1]+1]
        arc=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(segment,axis=0),axis=1))]
        keep=np.r_[True,np.diff(arc)>1e-12]
        arc=arc[keep];segment=segment[keep]
        slots=(start+np.arange(steps+1))%count
        if len(arc)==1 or arc[-1]<1e-12:
            out[slots]=segment[0]
        else:
            at=np.linspace(0.,arc[-1],steps+1)
            out[slots]=np.column_stack([np.interp(at,arc,segment[:,axis]) for axis in range(3)])
        assigned[slots]=True
    if not np.all(assigned):
        raise ValueError('Unassigned boundary EP slots')
    return out
