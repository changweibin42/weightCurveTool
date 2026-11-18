import ast,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class ProjectProtocolTests(unittest.TestCase):
 def test_actual_protocol_parser(self):
  code='''#include "ProjectPointsProtocol.h"
#include <sstream>
#include <cassert>
int main(){
 {std::istringstream s("1 2 1.25 2 3 -4 5 6");auto p=wctproject::parse(s);assert(p.size()==2&&p[0][0]==1.25&&p[1][0]==-4);}
 {std::istringstream s("1 0");assert(wctproject::parse(s).empty());}
 for(auto text:{"2 0","1 -1","1 1000001","1 1 1 2","1 1 nan 2 3","1 1 1 2 inf","1 0 tail"}){
 bool bad=false;try{std::istringstream s(text);wctproject::parse(s);}catch(const std::runtime_error&){bad=true;}assert(bad);}
}
'''
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'test.cpp';out=Path(d)/'test';src.write_text(code)
   subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'src'),str(src),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
 def test_actual_new_command_with_sdk_doubles(self):
  tree=ast.parse((ROOT/'tests/test_native_strict_command.py').read_text())
  prefix=next(n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='prefix' for t in n.targets))
  prefix='#include "ProjectPointsProtocol.h"\n'+prefix
  prefix+='\nstruct WctProjectPoints{MDoubleArray result;void setResult(const MDoubleArray& x){result=x;}bool doIt(const MArgList&);};\n'
  source=(ROOT/'src/CurveEdgeCutsCommand.cpp').read_text();body=source[source.index('MStatus WctProjectPoints::doIt'):]
  suffix='''
int main(){for(int mode:{0,2}){
 WctProjectPoints command;MArgList args{{"mesh",std::to_string(mode),"1 2 1.25 2 3 -4 5 6"}};
 assert(command.doIt(args));auto r=command.result.data;assert(r.size()==12);
 assert(r[0]==1.25&&r[1]==2&&r[2]==3&&r[3]==0);assert(r[4]==-4&&r[5]==5&&r[6]==6);
 assert(r[8]==-1&&r[9]==1&&r[10]==2&&r[11]==-2);
 args.a[1]="3";assert(!command.doIt(args));}}
'''
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'command.cpp';out=Path(d)/'command';src.write_text(prefix+body+suffix)
   subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'src'),str(src),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
if __name__=='__main__':unittest.main()
