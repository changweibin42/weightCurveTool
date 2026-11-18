from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QueryReuseRingTests(unittest.TestCase):
    def test_actual_cached_ring_union_equals_original_bfs(self):
        source = (ROOT / 'src/CurveEdgeCutsCommand.cpp').read_text()
        methods = source.split(' IDs expandOriginal(')[1].split('\n};\nstruct Curve')[0]
        prefix = r'''
#include <algorithm>
#include <cassert>
#include <iterator>
#include <map>
#include <random>
#include <set>
#include <vector>
using IDs=std::set<int>;
struct Surface{
 bool useQueryReuse=true;
 std::vector<IDs> graph;
 std::map<std::pair<int,int>,IDs> ringMemo;
 std::size_t ringMemoVertices=0;unsigned long ringMemoHits=0,ringMemoMisses=0;
'''
        suffix = r'''
};
int main(){
 Surface s;s.graph.resize(150);std::mt19937 rng(31415);
 for(int i=0;i<350;++i){int a=rng()%150,b=rng()%150;if(a!=b){s.graph[a].insert(b);s.graph[b].insert(a);}}
 for(int rings=0;rings<9;++rings)for(int trial=0;trial<200;++trial){
  IDs seeds{int(rng()%150)};if(trial%3)seeds.insert(rng()%150);
  assert(s.expand(seeds,rings)==s.expandOriginal(seeds,rings));
 }
 assert(s.ringMemoHits>0&&s.ringMemoVertices<=32768&&s.ringMemo.size()<=1024);
 Surface full;full.graph=s.graph;full.ringMemoVertices=32768;
 assert(full.expand({0,1},4)==full.expandOriginal({0,1},4));assert(full.ringMemo.empty());
 Surface original;original.graph=s.graph;original.useQueryReuse=false;
 assert(original.expand({0,1},4)==original.expandOriginal({0,1},4));assert(original.ringMemo.empty());
 Surface fresh;fresh.graph=s.graph;assert(fresh.ringMemo.empty());
 assert(s.expand({},3).empty());assert(s.expand({0,1,2},3)==s.expandOriginal({0,1,2},3));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / 'rings.cpp'
            binary = directory / 'rings'
            cpp.write_text(prefix + ' IDs expandOriginal(' + methods + suffix)
            subprocess.run([
                'g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                str(cpp), '-o', str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
