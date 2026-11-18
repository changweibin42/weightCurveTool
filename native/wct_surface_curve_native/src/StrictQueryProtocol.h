#pragma once
#include <cmath>
#include <istream>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace wctstrict {
struct Request {
 int count=0;
 std::vector<std::pair<int,int>> edges;
 std::vector<int> vertices;
};
inline Request parse(std::istream& input,int actualCount){
 int version,count,edgeCount,requestCount;
 if(!(input>>version>>count>>edgeCount>>requestCount)||version!=1||count!=actualCount||count<1||
    edgeCount<0||edgeCount>10000000||requestCount<0||requestCount>count)
  throw std::runtime_error("Invalid strict-query header");
 Request out;out.count=count;std::set<std::pair<int,int>> seen;
 for(int i=0;i<edgeCount;i++){
  int a,b;if(!(input>>a>>b)||a<0||b<=a||b>=count||!seen.emplace(a,b).second)
   throw std::runtime_error("Invalid strict-query edge");
  out.edges.emplace_back(a,b);
 }
 std::set<int> vertices;
 for(int i=0;i<requestCount;i++){
  int v;if(!(input>>v)||v<0||v>=count||!vertices.insert(v).second)
   throw std::runtime_error("Invalid strict-query vertex");
  out.vertices.push_back(v);
 }
 std::string trailing;if(input>>trailing)throw std::runtime_error("Unexpected strict-query trailer");
 return out;
}
}
