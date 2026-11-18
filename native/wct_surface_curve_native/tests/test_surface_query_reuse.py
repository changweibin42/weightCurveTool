from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SurfaceQueryReuseTests(unittest.TestCase):
    def test_actual_projection_method_memo_and_fallback_lifetimes(self):
        source = (ROOT / 'src/CurveEdgeCutsCommand.cpp').read_text()
        method = source.split(' MStatus projectFaces(')[1].split(' const IDs& polygon(')[0]
        method = ' MStatus projectFaces(' + method
        prefix = r'''
#include "ExactPointCache.h"
#include <memory>
#include <cassert>
#include <stdexcept>
using MStatus=bool;namespace MS{constexpr bool kSuccess=true;}
struct MPoint{double x=0,y=0,z=0;MPoint()=default;MPoint(double a,double b,double c):x(a),y(b),z(c){}MPoint operator*(int)const{return *this;}};
namespace MSpace{constexpr int kWorld=0;}
struct Mesh{int calls=0;bool good=true;bool getClosestPoint(MPoint p,MPoint& q,int,int* face){++calls;q={p.x,p.y,p.z+.25};*face=3;return good;}};
struct Hit{MPoint p;int face=0;bool ambiguous=false;};
struct BVH{bool ambiguous=false;int legacy=0,ordered=0,lastSeed=-2;
 template<class Query>Hit closest(MPoint p,Query q){++legacy;return {q(0),0,ambiguous};}
 template<class Query>Hit closestOrdered(MPoint p,Query q,int seed){++ordered;lastSeed=seed;return {q(0),0,ambiguous};}
};
struct Surface{
 Mesh mesh;bool useQueryReuse=false;int previousFace=-1;int inverseMatrix=0,worldMatrix=0;
 unsigned pointMemoHits=0,faceHits=0,faceFallbacks=0;int narrow=0;
 struct ProjectedPoint{MPoint point;int face;};
 wctreuse::PointCache<ProjectedPoint> pointMemo;
 std::unique_ptr<BVH> faceBVH=std::make_unique<BVH>();
 MPoint projectFace(int,MPoint q){++narrow;return q;}
'''
        suffix = r'''
};
int main(){
 MPoint p{1,2,3},out;int face=-1;
 Surface original;assert(original.projectFaces(p,out,&face));assert(original.projectFaces(p,out,&face));
 assert(original.faceBVH->legacy==2&&original.pointMemo.size()==0);
 Surface reuse;reuse.useQueryReuse=true;
 assert(reuse.projectFaces(p,out,&face));assert(reuse.projectFaces(p,out,&face));
 assert(reuse.faceBVH->ordered==1&&reuse.pointMemoHits==1&&out.z==3&&face==0);
 MPoint changed{std::nextafter(1.,2.),2,3};assert(reuse.projectFaces(changed,out,&face));
 assert(reuse.faceBVH->ordered==2&&reuse.faceBVH->lastSeed==0);
 Surface fresh;fresh.useQueryReuse=true;assert(fresh.projectFaces(p,out,&face));assert(fresh.pointMemoHits==0);
 Surface tied;tied.useQueryReuse=true;tied.faceBVH->ambiguous=true;
 assert(tied.projectFaces(p,out,&face));assert(out.z==3.25&&face==3&&tied.faceFallbacks==1);
 assert(tied.projectFaces(p,out,&face));assert(tied.mesh.calls==1&&tied.pointMemoHits==1);
 Surface failure;failure.useQueryReuse=true;failure.faceBVH->ambiguous=true;failure.mesh.good=false;
 assert(!failure.projectFaces(p,out,&face));assert(!failure.projectFaces(p,out,&face));
 assert(failure.pointMemo.size()==0&&failure.mesh.calls==2);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / 'surface.cpp'
            binary = directory / 'surface'
            cpp.write_text(prefix + method + suffix)
            subprocess.run([
                'g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-I' + str(ROOT / 'src'), str(cpp), '-o', str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
