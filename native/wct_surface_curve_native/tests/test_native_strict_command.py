import subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class NativeCommandTests(unittest.TestCase):
 def test_actual_new_command_body_with_sdk_doubles(self):
  source=(ROOT/'src/CurveEdgeCutsCommand.cpp').read_text();body=source[source.index('MStatus WctStrictBoundaryQueries::doIt'):].split('MStatus WctProjectPoints::doIt')[0]
  # Compile the actual added command body against minimal SDK doubles. This
  # catches C++ syntax/control-flow issues, not real Maya ABI or geometry.
  prefix=r'''
#include "StrictQueryProtocol.h"
#include <array>
#include <chrono>
#include <iomanip>
#include <locale>
#include <sstream>
#include <cassert>
#include <vector>
#include <set>
#include <cmath>
using MStatus=bool;namespace MS{constexpr bool kSuccess=true,kFailure=false;}
struct MString{std::string s;MString(const char* x):s(x){} MString(std::string x):s(x){}const char* asChar()const{return s.c_str();}MString operator+(const char* x)const{return s+x;}};
struct MGlobal{static void displayInfo(MString){}static void displayError(MString){}};
struct MArgList{std::vector<std::string> a;unsigned length()const{return a.size();}double asDouble(int i)const{return std::stod(a[i]);}int asInt(int i)const{return std::stoi(a[i]);}MString asString(int i)const{return a[i];}};
struct V{double x=0,y=0,z=0;V()=default;V(double a,double b,double c):x(a),y(b),z(c){}V operator-(V p)const{return {x-p.x,y-p.y,z-p.z};}double operator*(V p)const{return x*p.x+y*p.y+z*p.z;}V operator^(V p)const{return {y*p.z-z*p.y,z*p.x-x*p.z,x*p.y-y*p.x};}double length()const{return std::sqrt((*this)*(*this));}};
using MPoint=V;using MVector=V;namespace MSpace{constexpr int kWorld=0;}
struct Points:std::vector<V>{using std::vector<V>::vector;unsigned length()const{return size();}};
struct MDoubleArray{std::vector<double> data;void append(double x){data.push_back(x);}};
using IDs=std::set<int>;using Clock=std::chrono::steady_clock;
double seconds(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
void check(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
MString path(MString name){return name;}
bool intersects(const IDs&a,const IDs&b){for(int x:a)if(b.count(x))return true;return false;}
struct Mesh{bool getClosestPoint(V p,V& out,int,int* face){out=p;*face=0;return true;}};
struct Surface{
 Mesh mesh;Points points{{0,2,0},{1,-3,0},{9,1,0},{5,7,0}};std::vector<IDs> graph;bool useFaceBVH=false;
 double curveSeconds=0,pointSeconds=0,normalSeconds=0,tangentSeconds=0;unsigned curveCalls=0,pointCalls=0,normalCalls=0,tangentCalls=0,faceHits=0,faceFallbacks=0;
 Surface(MString,int mode){useFaceBVH=(mode&2)!=0;}
 bool projectFaces(V p,V& out,int* face){++faceHits;return mesh.getClosestPoint(p,out,0,face);}
 IDs polygon(int){return {0,1};}
 V normal(V){return {0,0,1};}
 IDs expand(IDs out,int rings){IDs frontier=out;for(int k=0;k<rings;k++){IDs next;for(int v:frontier)for(int x:graph.at(v))if(!out.count(x))next.insert(x);out.insert(next.begin(),next.end());frontier=std::move(next);}return out;}
};
struct MFnNurbsCurve{MFnNurbsCurve(MString){}V closestPoint(V p,double* param,double,int,bool* status){*status=true;*param=p.x;return {p.x,0,0};}V tangent(double p,int,bool* status){*status=true;return p==5?V{0,0,0}:V{1,0,0};}};
struct WctStrictBoundaryQueries{MDoubleArray result;void setResult(const MDoubleArray& x){result=x;}bool doIt(const MArgList&);};
'''
  suffix=r'''
int main(){for(int mode=0;mode<4;mode++){
 WctStrictBoundaryQueries c;MArgList args{{"mesh","curve","0.000001","4",std::to_string(mode),"1 4 2 4 0 1 1 3 0 1 2 3"}};
 assert(c.doIt(args));auto r=c.result.data;assert(r.size()==15);
 assert(r[0]==0&&r[1]==1&&r[2]==2);assert(r[3]==1&&r[4]==1&&r[5]==-3);
 assert(r[6]==2&&r[7]==0);assert(r[9]==3&&r[10]==0);assert(r[12]==-1&&r[13]==1&&r[14]==4);
 args.a[4]="9";assert(!c.doIt(args));
}}
'''
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'command.cpp';out=Path(d)/'command';src.write_text(prefix+body+suffix)
   subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'src'),str(src),'-o',str(out)],check=True)
   subprocess.run([str(out)],check=True)
if __name__=='__main__':unittest.main()
