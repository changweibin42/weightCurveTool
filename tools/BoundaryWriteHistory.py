# -*- coding: utf-8 -*-
"""Restore only vertices previously written by this tool and still unchanged.

Keep state per mesh/influence/session in the caller. Commit only after a
successful skin write. Manual edits and Undo invalidate ownership per vertex.
"""
import numpy as np


def prepare_write(previous, mask, desired, picked, parent):
    mask = np.asarray(mask, dtype=bool)
    picked, parent = np.asarray(picked), np.asarray(parent)
    desired = np.asarray(desired, dtype=float).copy()
    baseline = picked.copy()
    restore = np.zeros(len(mask), dtype=bool)
    if previous is not None and previous['mask'].shape == mask.shape:
        owned = (previous['mask']
                 & np.isclose(picked, previous['picked'], atol=1e-7, rtol=1e-6)
                 & np.isclose(parent, previous['parent'], atol=1e-7, rtol=1e-6))
        baseline[owned] = previous['baseline'][owned]
        restore = owned & ~mask
        desired[restore] = baseline[restore]
    return desired, mask | restore, baseline


def committed_state(mask, baseline, picked, parent):
    return dict(mask=np.asarray(mask, bool).copy(), baseline=np.asarray(baseline).copy(),
                picked=np.asarray(picked).copy(), parent=np.asarray(parent).copy())
