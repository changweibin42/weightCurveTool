#pragma once
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <unordered_map>

namespace wctreuse {
using Key=std::array<std::uint64_t,3>;
struct KeyHash {
 std::size_t operator()(const Key& key)const noexcept{
  std::size_t hash=1469598103934665603ull;
  for(auto value:key){hash^=std::size_t(value);hash*=1099511628211ull;}
  return hash;
 }
};
inline bool key(double x,double y,double z,Key& out){
 if(!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(z))return false;
 const double coordinates[]={x,y,z};
 static_assert(sizeof(double)==sizeof(std::uint64_t),"Expected IEEE binary64 storage");
 for(int i=0;i<3;++i)std::memcpy(&out[i],&coordinates[i],sizeof(double));
 return true;
}
// Owned by one read-only command. No scene/pose state survives the command.
// Store only successful results. No rounding, tolerances or quantization.
template<class Value> class PointCache {
 std::unordered_map<Key,Value,KeyHash> values;
 std::size_t limit;
public:
 explicit PointCache(std::size_t capacity=16384):limit(capacity){}
 const Value* find(double x,double y,double z)const{
  Key k;if(!key(x,y,z,k))return nullptr;
  auto it=values.find(k);return it==values.end()?nullptr:&it->second;
 }
 void insert(double x,double y,double z,const Value& value){
  Key k;if(values.size()>=limit||!key(x,y,z,k))return;
  values.emplace(k,value);
 }
 std::size_t size()const{return values.size();}
};
}
