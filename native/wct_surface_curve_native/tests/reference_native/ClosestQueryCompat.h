#pragma once
// SDK-compatible overload selection: older headers can omit the optional
// closest-query accelerator argument. They retain the exact legacy call.
namespace wctquery {
template<class Mesh,class Point,class Space,class Accel>
auto closest(Mesh& mesh,const Point& p,Point& q,Space space,int* face,Accel* accel,bool& accelerated,int)
 -> decltype(mesh.getClosestPoint(p,q,space,face,accel)) {
 accelerated=true;return mesh.getClosestPoint(p,q,space,face,accel);
}
template<class Mesh,class Point,class Space,class Accel>
auto closest(Mesh& mesh,const Point& p,Point& q,Space space,int* face,Accel*,bool& accelerated,long)
 -> decltype(mesh.getClosestPoint(p,q,space,face)) {
 accelerated=false;return mesh.getClosestPoint(p,q,space,face);
}
template<class Mesh,class Point,class Normal,class Space,class Accel>
auto normal(Mesh& mesh,const Point& p,Normal& n,Space space,Accel* accel,bool& accelerated,int)
 -> decltype(mesh.getClosestNormal(p,n,space,nullptr,accel)) {
 accelerated=true;return mesh.getClosestNormal(p,n,space,nullptr,accel);
}
template<class Mesh,class Point,class Normal,class Space,class Accel>
auto normal(Mesh& mesh,const Point& p,Normal& n,Space space,Accel*,bool& accelerated,long)
 -> decltype(mesh.getClosestNormal(p,n,space)) {
 accelerated=false;return mesh.getClosestNormal(p,n,space);
}
}
