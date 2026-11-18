#pragma once
#include <array>
#include <cmath>
#include <istream>
#include <stdexcept>
#include <string>
#include <vector>
namespace wctproject {
inline std::vector<std::array<double,3>> parse(std::istream& input){
 int version,count;
 if(!(input>>version>>count)||version!=1||count<0||count>1000000)
  throw std::runtime_error("Invalid project-point header");
 std::vector<std::array<double,3>> points;points.reserve(count);
 for(int i=0;i<count;i++){
  double x,y,z;if(!(input>>x>>y>>z)||!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(z))
   throw std::runtime_error("Invalid project-point coordinate");
  points.push_back({x,y,z});
 }
 std::string trailing;if(input>>trailing)throw std::runtime_error("Unexpected project-point trailer");
 return points;
}
}
