"""End-to-end numeric-call benchmark INCLUDING conversions/list output.

Not a Maya scene benchmark. Prints no promised UI/frame-rate improvement.
"""
import json
import statistics
import time
from check_helpers import load_profiles,load_reference
import numpy as np

def measure(fn,repeats=5):
    fn();samples=[]
    for _ in range(repeats):
        start=time.perf_counter();fn();samples.append(time.perf_counter()-start)
    return statistics.median(samples)*1000.

if __name__=='__main__':
    P=load_profiles();R=load_reference()
    if not P.get_native_status()['enabled']:raise RuntimeError('Native backend is not enabled')
    xs=[0,.5,.5,1.];ys=[0,0,1,1.];rows=[]
    for count in (3722,20000):
        x=np.linspace(-3.,3.,count);matrix=np.array([x,x-1.,x-2.,x-3.]);budget=np.linspace(.1,1.,count)
        for name,args in [('_ik_weights',(x,xs,ys,2.)),('_soft_weights',(x,xs,ys,2.)),
                          ('_split_weights',(matrix,budget,xs,ys,2.))]:
            expected=getattr(R,name)(*args);actual=getattr(P,name)(*args)
            np.testing.assert_allclose(actual,expected,atol=1e-12,rtol=1e-12)
            old=measure(lambda:getattr(R,name)(*args));new=measure(lambda:getattr(P,name)(*args))
            row=dict(function=name,vertices=count,python_ms=old,native_ms=new,kernel_speedup=old/new)
            rows.append(row);print(json.dumps(row))
    print('These are numeric-call timings, not total Maya-operation timings.')
