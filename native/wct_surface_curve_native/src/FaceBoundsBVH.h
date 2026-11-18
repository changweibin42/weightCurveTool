#pragma once
#include "DoubleMeshBVH.h"
#include <queue>
#include <unordered_map>
// Bounds-only broad phase; final points are supplied by the Maya callback.
namespace wctface {
using wctbvh::Vec;using wctbvh::Box;
struct Item {Box bounds;int face=-1;};
struct Node {Box bounds;int begin=0,end=0,left=-1,right=-1;};
struct Hit {Vec p;int face=-1;double distance2=INFINITY;bool ambiguous=false;int queries=0;};
class BVH {
 std::vector<Item> items;std::vector<Node> nodes;double guard=0;
 double coordinateScale=1;
 std::unordered_map<int,int> faceIndex;
 // Legacy Maya points in the captured case are float-rounded. A double-only
 // comparison would falsely regard their near-equal face distances as unique.
 // This envelope ONLY increases exact global fallbacks, never acceptance eps.
 double roundoffRadius(Vec point)const{
  double scale=std::max({coordinateScale,std::abs(point.x),std::abs(point.y),std::abs(point.z),1.});
  float f=static_cast<float>(scale);
  double ulp=double(std::nextafter(f,std::numeric_limits<float>::infinity()))-double(f);
  return 8.*std::sqrt(3.)*ulp;
 }
 double margin(double distance2,double radius)const{
  if(!std::isfinite(distance2))return INFINITY;
  return guard+8.*std::sqrt(std::max(0.,distance2))*radius+4.*radius*radius;
 }
 int build(int begin,int end){
  Node n;n.begin=begin;n.end=end;
  for(int i=begin;i<end;i++){n.bounds.add(items[i].bounds.lo);n.bounds.add(items[i].bounds.hi);}
  int index=int(nodes.size());nodes.push_back(n);
  if(end-begin>4){Vec extent=n.bounds.hi-n.bounds.lo;int axis=extent.y>extent.x?1:0;if(extent.z>extent[axis])axis=2;int mid=(begin+end)/2;
   std::nth_element(items.begin()+begin,items.begin()+mid,items.begin()+end,[axis](const Item&a,const Item&b){return a.bounds.lo[axis]+a.bounds.hi[axis]<b.bounds.lo[axis]+b.bounds.hi[axis];});
   int left=build(begin,mid),right=build(mid,end);nodes[index].left=left;nodes[index].right=right;
  }return index;
 }
public:
 explicit BVH(std::vector<Item> data,bool buildSeedIndex=false):items(std::move(data)){
  if(items.empty())throw std::runtime_error("Face BVH requires polygons");
  for(const auto& item:items)if(!wctbvh::finite(item.bounds.lo)||!wctbvh::finite(item.bounds.hi))throw std::runtime_error("Invalid face bounds");
  build(0,int(items.size()));
  if(buildSeedIndex)for(int i=0;i<int(items.size());++i)faceIndex.emplace(items[i].face,i);
  const Box& bounds=nodes[0].bounds;
  coordinateScale=std::max({std::abs(bounds.lo.x),std::abs(bounds.lo.y),std::abs(bounds.lo.z),std::abs(bounds.hi.x),std::abs(bounds.hi.y),std::abs(bounds.hi.z),1.});
  guard=256*std::numeric_limits<double>::epsilon()*std::max(1.,wctbvh::squared(nodes[0].bounds.hi-nodes[0].bounds.lo));
 }
 template<class Query> Hit closest(Vec point,Query query)const{
  if(!wctbvh::finite(point))throw std::runtime_error("Non-finite query point");
  Hit best;double radius=roundoffRadius(point);
  if(!std::isfinite(radius)){best.ambiguous=true;return best;}
  std::vector<int> stack{0};bool invalid=false;
  while(!stack.empty()){
   int id=stack.back();stack.pop_back();const Node& node=nodes[id];if(node.bounds.distance2(point)>best.distance2+margin(best.distance2,radius))continue;
   if(node.left<0){
    // Visit nearby boxes first. Ordering only affects speed; ties fall back.
    std::vector<std::pair<double,int>> order;
    for(int i=node.begin;i<node.end;i++)order.push_back({items[i].bounds.distance2(point),i});
    std::sort(order.begin(),order.end());
    for(auto entry:order){
     if(entry.first>best.distance2+margin(best.distance2,radius))continue;
     if(++best.queries>1024){best.ambiguous=true;return best;}
     const auto& item=items[entry.second];Vec p=query(item.face);
     // Never prune based on a callback result outside the face's broad bounds.
     if(!wctbvh::finite(p)||item.bounds.distance2(p)>guard+radius*radius){invalid=true;continue;}
     double distance=wctbvh::squared(point-p);
     double comparison=margin(std::min(distance,best.distance2),radius);
     if(best.face<0||distance<best.distance2-comparison){best.p=p;best.face=item.face;best.distance2=distance;best.ambiguous=false;}
     else if(std::abs(distance-best.distance2)<=comparison){if(item.face!=best.face)best.ambiguous=true;
      if(distance<best.distance2){best.p=p;best.face=item.face;best.distance2=distance;}
     }
    }
   }else{
    double left=nodes[node.left].bounds.distance2(point),right=nodes[node.right].bounds.distance2(point);
    if(left<right){stack.push_back(node.right);stack.push_back(node.left);}else{stack.push_back(node.left);stack.push_back(node.right);}
   }
  }
  best.ambiguous=best.ambiguous||invalid||best.face<0;return best;
 }

 // Best-first broad phase for coherent edge probes. A queue entry is either
 // a BVH node or one exact original face bound. No face beyond the original
 // conservative pruning envelope is accepted, and ties still fall back.
 template<class Query> Hit closestOrdered(Vec point,Query query,int seedFace=-1)const{
  if(!wctbvh::finite(point))throw std::runtime_error("Non-finite query point");
  Hit best;double radius=roundoffRadius(point);
  if(!std::isfinite(radius)){best.ambiguous=true;return best;}
  struct Entry{double lower;int index;bool face;};
  struct Later{bool operator()(const Entry&a,const Entry&b)const{
   if(a.lower!=b.lower)return a.lower>b.lower;
   if(a.face!=b.face)return a.face<b.face;
   return a.index>b.index;
  }};
  std::priority_queue<Entry,std::vector<Entry>,Later> pending;
  pending.push({nodes[0].bounds.distance2(point),0,false});
  bool invalid=false,first=true;int seeded=-1;
  auto consider=[&](int index){
   if(++best.queries>1024)return false;
   const auto& item=items[index];Vec p=query(item.face);
   if(!wctbvh::finite(p)||item.bounds.distance2(p)>guard+radius*radius){invalid=true;return true;}
   double distance=wctbvh::squared(point-p);
   double comparison=margin(std::min(distance,best.distance2),radius);
   if(best.face<0||distance<best.distance2-comparison){best.p=p;best.face=item.face;best.distance2=distance;best.ambiguous=false;}
   else if(std::abs(distance-best.distance2)<=comparison){
    if(item.face!=best.face)best.ambiguous=true;
    if(distance<best.distance2){best.p=p;best.face=item.face;best.distance2=distance;}
   }
   return true;
  };
  while(!pending.empty()){
   Entry entry=pending.top();pending.pop();
   if(entry.lower>best.distance2+margin(best.distance2,radius))break;
   if(entry.face){
    if(first){
     first=false;auto found=faceIndex.find(seedFace);
     if(seedFace>=0&&found!=faceIndex.end()
          &&items[found->second].bounds.distance2(point)<=entry.lower+guard){
      seeded=found->second;
      if(!consider(seeded)){best.ambiguous=true;return best;}
     }
    }
    if(entry.index!=seeded&&!consider(entry.index)){best.ambiguous=true;return best;}
   }else{
    const Node& node=nodes[entry.index];
    if(node.left<0){
     for(int i=node.begin;i<node.end;++i)pending.push({items[i].bounds.distance2(point),i,true});
    }else{
     pending.push({nodes[node.left].bounds.distance2(point),node.left,false});
     pending.push({nodes[node.right].bounds.distance2(point),node.right,false});
    }
   }
  }
  best.ambiguous=best.ambiguous||invalid||best.face<0;return best;
 }
};
}
