import subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class StrictProtocolTests(unittest.TestCase):
 def test_actual_cpp_payload_parser(self):
  code='''#include "StrictQueryProtocol.h"
#include <sstream>
#include <cassert>
int main(){
{std::istringstream s("1 3 2 2 0 1 1 2 0 2");auto r=wctstrict::parse(s,3);assert(r.count==3&&r.edges.size()==2&&r.vertices[1]==2);}
for(auto text:{"2 3 0 0","1 4 0 0","1 3 1 0 1 0","1 3 2 0 0 1 0 1","1 3 0 2 0 0","1 3 0 1 3","1 3 0 0 tail","1 3 0 4","1 3 0 1"}){
 bool failed=false;try{std::istringstream s(text);wctstrict::parse(s,3);}catch(const std::runtime_error&){failed=true;}assert(failed);}
}
'''
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'test.cpp';out=Path(d)/'test';src.write_text(code)
   subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'src'),str(src),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
 def test_node_id_and_existing_query_headers_unchanged(self):
  self.assertIn('0x00142A04',(ROOT/'src/node_ids.h').read_text())
  # Fixture copies below are byte-authoritative previous accepted sources.
  for name in ('ClosestQueryCompat.h','CurveMath.h'):
   self.assertEqual((ROOT/'src'/name).read_bytes(),(ROOT/'tests/reference_native'/name).read_bytes())
if __name__=='__main__':unittest.main()
