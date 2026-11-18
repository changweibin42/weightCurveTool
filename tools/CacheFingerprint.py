"""Exact fingerprints shared by geometry and curve query caches."""
import hashlib
import json

import numpy as np


def fingerprint(parts, *, sort_keys=False):
    digest = hashlib.blake2b(digest_size=32)
    for part in parts:
        if isinstance(part, np.ndarray):
            value = np.ascontiguousarray(part)
            digest.update(str((value.shape, value.dtype.str)).encode('ascii'))
            digest.update(value.tobytes())
        else:
            digest.update(
                json.dumps(part, sort_keys=sort_keys, allow_nan=False).encode('utf-8')
            )
        digest.update(b'\0')
    return digest.digest()
