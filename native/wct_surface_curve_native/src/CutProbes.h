#pragma once
#include <vector>
#include <cmath>
#include <algorithm>
namespace wctcuts {
struct Eval {bool valid=false;double side=0,distance=0;};
// Same fixed probe count, bisection count, tolerances and midpoint convention
// as StrictCurveRegion._edge_cuts. No early exit based on endpoint signs.
template<class Query>
std::vector<double> cuts(Eval ra,Eval rb,double length,double median,double eps,Query query){
 std::vector<double> out;
 if(ra.valid&&ra.distance<=eps)out.push_back(0);
 if(rb.valid&&rb.distance<=eps)out.push_back(1);
 int steps=std::max(4,std::min(32,int(std::ceil(length/std::max(median*.25,eps)))));
 std::vector<std::pair<double,Eval>> probes{{0,ra}};
 for(int k=1;k<steps;k++){
  double u=double(k)/steps;Eval r=query(u);probes.push_back({u,r});
  if(r.valid&&r.distance<=eps)out.push_back(u);
 }probes.push_back({1,rb});
 for(size_t i=1;i<probes.size();i++){
  double lo=probes[i-1].first,hi=probes[i].first;
  Eval left=probes[i-1].second,right=probes[i].second;
  if(!left.valid||!right.valid||left.side*right.side>=0)continue;
  double lv=left.side;Eval result;
  for(int it=0;it<32;it++){
   double mid=(lo+hi)*.5;result=query(mid);
   if(!result.valid)break;
   if((hi-lo)*length<=eps||std::abs(result.side)<=eps)break;
   if(lv*result.side<=0)hi=mid;else{lo=mid;lv=result.side;}
  }
  if(result.valid&&result.distance<=std::max(eps*10,median*.025))out.push_back((lo+hi)*.5);
 }return out;
}
}
