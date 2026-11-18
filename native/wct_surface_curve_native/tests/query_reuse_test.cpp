#include "FaceBoundsBVH.h"
#include "ExactPointCache.h"
#include "legacy_face_bounds.h"
#include <cassert>
#include <iostream>
#include <random>
using wctbvh::Vec;

bool same(Vec a,Vec b){return a.x==b.x&&a.y==b.y&&a.z==b.z;}

int main(){
 wctreuse::PointCache<int> cache(2);
 cache.insert(1.,2.,3.,7);assert(*cache.find(1.,2.,3.)==7);
 assert(!cache.find(std::nextafter(1.,2.),2.,3.));
 cache.insert(0.,0.,0.,8);assert(!cache.find(-0.,0.,0.));
 cache.insert(9.,9.,9.,9);assert(cache.size()==2&&!cache.find(9.,9.,9.));
 cache.insert(NAN,0,0,1);assert(!cache.find(NAN,0,0));
 assert(!cache.find(INFINITY,0,0));
 wctreuse::PointCache<int> fresh;assert(!fresh.find(1.,2.,3.));

 auto box=[](int face,Vec a,Vec b){wctface::Item i;i.face=face;i.bounds.add(a);i.bounds.add(b);return i;};
 auto tie=std::vector<wctface::Item>{box(0,{0,0,0},{1,1,0}),box(1,{0,0,2},{1,1,2})};
 wctface::BVH tied(tie,true);
 for(int seed:{-1,0,1,999}){
  auto hit=tied.closestOrdered({.5,.5,1},[](int face){return Vec{.5,.5,double(face)*2};},seed);
  assert(hit.ambiguous);
 }
 wctface::BVH broken({box(0,{0,0,0},{1,1,1})},true);
 assert(broken.closestOrdered({0,0,0},[](int){return Vec{2,2,2};},0).ambiguous);

 // Compare both accepted results and ambiguity decisions against the actual
 // previous header. The projection callback simulates Maya float rounding.
 std::mt19937_64 rng(20261117);std::uniform_real_distribution<double> unit(0,1);
 std::vector<wctbvh::Tri> triangles;
 std::vector<wctface::Item> items;
 std::vector<legacy_wctface::Item> legacyItems;
 for(int y=0;y<16;++y)for(int x=0;x<16;++x){
  double z=.5*std::sin(x*.4)+.3*std::cos(y*.7);
  for(int side=0;side<2;++side){
   wctbvh::Tri t;
   t.a={double(x),double(y),z};
   t.b={double(x+1),double(y),z+.2};
   t.c=side?Vec{double(x+1),double(y+1),z+.1}:Vec{double(x),double(y+1),z-.1};
   if(side)t.a={double(x),double(y+1),z-.1};
   t.face=int(triangles.size());triangles.push_back(t);
   wctface::Item item;item.face=t.face;item.bounds.add(t.a);item.bounds.add(t.b);item.bounds.add(t.c);items.push_back(item);
   legacy_wctface::Item old;old.face=item.face;old.bounds=item.bounds;legacyItems.push_back(old);
  }
 }
 wctface::BVH tree(items,true);legacy_wctface::BVH old(legacyItems);
 long oldCalls=0,newCalls=0;int previous=-1;
 for(int k=0;k<10000;++k){
  Vec p{unit(rng)*16,unit(rng)*16,(unit(rng)-.5)*3};
  if(k%4)p={8.+.00001*(k%100),7.+.00002*(k%100),.5};
  auto project=[&](int face){Vec q=wctbvh::triangle(p,triangles[face]).p;return Vec{double(float(q.x)),double(float(q.y)),double(float(q.z))};};
  auto before=old.closest(p,[&](int f){++oldCalls;return project(f);});
  auto after=tree.closestOrdered(p,[&](int f){++newCalls;return project(f);},previous);
  assert(before.ambiguous==after.ambiguous);
  if(!before.ambiguous){assert(before.face==after.face);assert(same(before.p,after.p));}
  previous=after.face;
 }
 std::cout<<"10000 seeded/unseeded queries agree; callbacks "<<oldCalls<<" -> "<<newCalls<<"\n";
}
