# cython: language_level=3, boundscheck=False, wraparound=False
"""Optional scalar-compatible projection profiles. No Maya or NumPy C API."""
from libc.math cimport pow, isfinite
import numpy as np

BACKEND_API_VERSION = 1

cdef inline double _cubic(const double[::1] p, double t) noexcept nogil:
    cdef double u = 1.0-t
    # Preserve the reference expression/order and binary64 math. No LUT,
    # fast-math, altered inverse tolerance or centre/endpoint adjustment.
    return p[0]*pow(u,3.0)+3.0*p[1]*t*pow(u,2.0)+3.0*p[2]*pow(t,2.0)*u+p[3]*pow(t,3.0)

cdef inline double _weight(double x, const double[::1] xs, const double[::1] ys) noexcept nogil:
    cdef double lo=0.0, hi=1.0, mid, error
    cdef int iteration
    if x<=0.0:return ys[0]
    if x>=1.0:return ys[3]
    for iteration in range(48):
        mid=(lo+hi)*0.5
        error=_cubic(xs,mid)-x
        if error>1e-4:hi=mid
        elif error< -1e-4:lo=mid
        else:return _cubic(ys,mid)
    return _cubic(ys,(lo+hi)*0.5)

cdef void _validate(const double[::1] xs,const double[::1] ys,double radius):
    cdef int i
    if xs.shape[0]!=4 or ys.shape[0]!=4:
        raise ValueError('Exactly four X/Y control values are required')
    if not isfinite(radius) or radius==0.0:
        raise ValueError('Radius must be finite and nonzero')
    for i in range(4):
        if not isfinite(xs[i]) or not isfinite(ys[i]):
            raise ValueError('Controls must be finite')

def profile_batch(const double[::1] values,const double[::1] xs,const double[::1] ys,
                  double radius,double offset):
    """Return float64 values for the same get_weight(value/radius+offset)."""
    _validate(xs,ys,radius)
    if not isfinite(offset):raise ValueError('Offset must be finite')
    cdef Py_ssize_t i,n=values.shape[0]
    result=np.empty(n,dtype=np.float64)
    cdef double[::1] out=result
    with nogil:
        for i in range(n):out[i]=_weight(values[i]/radius+offset,xs,ys)
    return result

def split_batch(const double[:,::1] matrix,const double[::1] budget,
                const double[::1] xs,const double[::1] ys,double radius):
    """Reference cumulative FK split. Preserve its unclamped differences."""
    _validate(xs,ys,radius)
    cdef Py_ssize_t layers=matrix.shape[0],count=matrix.shape[1],v,j
    cdef double previous,current
    if budget.shape[0]!=count:raise ValueError('Budget length differs from vertex count')
    result=np.empty((count,layers+1),dtype=np.float64)
    cdef double[:,::1] out=result
    with nogil:
        for v in range(count):
            previous=1.0
            for j in range(layers):
                current=_weight(matrix[j,v]/radius+0.5,xs,ys)
                out[v,j]=(previous-current)*budget[v]
                previous=current
            out[v,layers]=previous*budget[v]
    return result
