import ast
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QueryReuseCommandTests(unittest.TestCase):
    def test_actual_command_accepts_old_abi_and_validates_new_mode(self):
        tree = ast.parse((ROOT / 'tests/test_native_strict_command.py').read_text())
        prefix = next(n.value.value for n in ast.walk(tree)
                      if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'prefix' for t in n.targets))
        prefix = '#include "CutProbes.h"\n#include "ExactPointCache.h"\n#include <map>\n#include <memory>\n' + prefix
        prefix = prefix.replace('V operator-(V p)const',
                                'V operator+(V p)const{return {x+p.x,y+p.y,z+p.z};}'
                                'V operator*(double s)const{return {x*s,y*s,z*s};}V operator-(V p)const')
        prefix = prefix.replace('struct Mesh{', 'struct Mesh{int numPolygons()const{return 1;}')
        prefix = prefix.replace('struct Surface{', 'bool lastReuse=false;\nstruct Surface{')
        prefix = prefix.replace('Surface(MString,int mode){useFaceBVH=(mode&2)!=0;}', r'''
 bool useQueryReuse=false,pointAccelerated=false,normalAccelerated=false,useDoubleBVH=false;
 std::map<int,int> faceMeshes;
 double faceBuildSeconds=0,bvhBuildSeconds=0;
 unsigned faceQueries=0,bvhHits=0,bvhFallbacks=0,pointMemoHits=0,ringMemoHits=0,ringMemoMisses=0;
 wctreuse::PointCache<int> pointMemo;
 Surface(MString,int mode=-1,bool reuse=false){useFaceBVH=(mode&2)!=0;useQueryReuse=reuse;lastReuse=reuse;}
''')
        prefix += r'''
using wctcuts::Eval;
struct Sample{MPoint p;int face;};
struct Curve{
 Surface& surface;std::vector<Sample> samples;IDs nearVertices;std::map<int,Eval> endpoints;
 Curve(Surface& s,MString,double,int):surface(s){}
 void prepare(){for(unsigned i=0;i<surface.points.length();++i)nearVertices.insert(i);}
 Eval evaluate(MPoint p,const IDs&){return {true,p.y,std::abs(p.y)};}
 Eval endpoint(int v){auto e=evaluate(surface.points[v],{});endpoints[v]=e;return e;}
};
struct WctCurveEdgeCuts{MDoubleArray result;void setResult(const MDoubleArray& x){result=x;}bool doIt(const MArgList&);};
'''
        source = (ROOT / 'src/CurveEdgeCutsCommand.cpp').read_text()
        body = 'MStatus WctCurveEdgeCuts::doIt' + source.split('MStatus WctCurveEdgeCuts::doIt')[1].split('MStatus WctStrictBoundaryQueries::doIt')[0]
        suffix = r'''
int main(){
 for(int mask:{1,2,4,7}){
  std::ostringstream data;data<<"2 4 2 "<<mask<<" 0 1 1 1 2 1";
  for(int i=0;i<3;++i)data<<" 1 0 0 0 0";
  MArgList args{{"mesh","ridge","fwd","bwd","0.000001","1","4",data.str()}};
  WctCurveEdgeCuts old;assert(old.doIt(args));assert(!lastReuse);
  args.a.push_back("1");WctCurveEdgeCuts reuse;assert(reuse.doIt(args));assert(lastReuse);
  assert(old.result.data==reuse.result.data);
  for(unsigned i=0;i+6<reuse.result.data.size();i+=6){
   if(reuse.result.data[i]==1){int curve=int(reuse.result.data[i+1]);assert(mask&(1<<curve));}
  }
  args.a[8]="2";assert(!reuse.doIt(args));
  args.a[8]="0";assert(reuse.doIt(args));assert(!lastReuse);
  args.a.push_back("0");assert(!reuse.doIt(args));
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / 'command.cpp'
            binary = directory / 'command'
            cpp.write_text(prefix + body + suffix)
            subprocess.run([
                'g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-I' + str(ROOT / 'src'), str(cpp), '-o', str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
