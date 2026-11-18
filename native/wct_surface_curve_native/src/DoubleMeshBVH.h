#pragma once
// Double-precision, per-batch triangle BVH. No Maya or float dependency.
#include <vector>
#include <array>
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
namespace wctbvh {
struct Vec {
 double x=0,y=0,z=0;
 Vec()=default;Vec(double a,double b,double c):x(a),y(b),z(c){}
 Vec operator+(Vec b)const{return {x+b.x,y+b.y,z+b.z};}
 Vec operator-(Vec b)const{return {x-b.x,y-b.y,z-b.z};}
 Vec operator*(double s)const{return {x*s,y*s,z*s};}
 double operator[](int i)const{return i==0?x:i==1?y:z;}
};
inline double dot(Vec a,Vec b){return a.x*b.x+a.y*b.y+a.z*b.z;}
inline Vec cross(Vec a,Vec b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
inline double squared(Vec a){return dot(a,a);}
inline bool finite(Vec p){return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z);}
struct Tri {Vec a,b,c;int face=-1;};
struct Projection {Vec p;double minBary=0;bool degenerate=false;};
inline Vec segment(Vec p,Vec a,Vec b){Vec d=b-a;double s=squared(d);return s>0?a+d*std::clamp(dot(p-a,d)/s,0.,1.):a;}
inline Projection triangle(Vec p,const Tri& t){
 Vec ab=t.b-t.a,ac=t.c-t.a;
 double edgeScale=std::max(squared(ab),squared(ac));
 if(edgeScale==0||squared(cross(ab,ac))<=1e-26*edgeScale*edgeScale){
  Vec q=segment(p,t.a,t.b),r=segment(p,t.b,t.c),s=segment(p,t.c,t.a);
  if(squared(p-r)<squared(p-q))q=r;
  if(squared(p-s)<squared(p-q))q=s;
  return {q,0,true};
 }
 Vec ap=p-t.a;double d1=dot(ab,ap),d2=dot(ac,ap);
 if(d1<=0&&d2<=0)return {t.a,0,false};
 Vec bp=p-t.b;double d3=dot(ab,bp),d4=dot(ac,bp);
 if(d3>=0&&d4<=d3)return {t.b,0,false};
 double vc=d1*d4-d3*d2;
 if(vc<=0&&d1>=0&&d3<=0){double v=d1/(d1-d3);return {t.a+ab*v,0,false};}
 Vec cp=p-t.c;double d5=dot(ab,cp),d6=dot(ac,cp);
 if(d6>=0&&d5<=d6)return {t.c,0,false};
 double vb=d5*d2-d1*d6;
 if(vb<=0&&d2>=0&&d6<=0){double w=d2/(d2-d6);return {t.a+ac*w,0,false};}
 double va=d3*d6-d5*d4;
 if(va<=0&&(d4-d3)>=0&&(d5-d6)>=0){double w=(d4-d3)/((d4-d3)+(d5-d6));return {t.b+(t.c-t.b)*w,0,false};}
 double inv=1./(va+vb+vc),v=vb*inv,w=vc*inv;
 return {t.a+ab*v+ac*w,std::min({1.-v-w,v,w}),false};
}
struct Box {
 Vec lo{INFINITY,INFINITY,INFINITY},hi{-INFINITY,-INFINITY,-INFINITY};
 void add(Vec p){lo={std::min(lo.x,p.x),std::min(lo.y,p.y),std::min(lo.z,p.z)};hi={std::max(hi.x,p.x),std::max(hi.y,p.y),std::max(hi.z,p.z)};}
 double distance2(Vec p)const{double r=0;for(int i=0;i<3;i++){double d=p[i]<lo[i]?lo[i]-p[i]:p[i]>hi[i]?p[i]-hi[i]:0;r+=d*d;}return r;}
};
struct Node {Box bounds;int begin=0,end=0,left=-1,right=-1;};
struct Hit {Vec p;int face=-1;double distance2=INFINITY;bool ambiguous=false;};
class BVH {
 std::vector<Tri> triangles;
 std::vector<Node> nodes;
 double guard=0;
 int build(int begin,int end){
  Node n;n.begin=begin;n.end=end;
  for(int i=begin;i<end;i++){n.bounds.add(triangles[i].a);n.bounds.add(triangles[i].b);n.bounds.add(triangles[i].c);}
  int index=int(nodes.size());nodes.push_back(n);
  if(end-begin>8){
   Vec extent=n.bounds.hi-n.bounds.lo;int axis=extent.y>extent.x?1:0;if(extent.z>extent[axis])axis=2;
   int mid=(begin+end)/2;
   std::nth_element(triangles.begin()+begin,triangles.begin()+mid,triangles.begin()+end,[axis](const Tri&a,const Tri&b){return (a.a[axis]+a.b[axis]+a.c[axis])<(b.a[axis]+b.b[axis]+b.c[axis]);});
   int left=build(begin,mid),right=build(mid,end);nodes[index].left=left;nodes[index].right=right;
  }return index;
 }
public:
 explicit BVH(std::vector<Tri> data):triangles(std::move(data)){
  if(triangles.empty())throw std::runtime_error("BVH requires mesh triangles");
  for(auto&t:triangles)if(!finite(t.a)||!finite(t.b)||!finite(t.c))throw std::runtime_error("Non-finite mesh coordinates");
  build(0,int(triangles.size()));
  guard=128*std::numeric_limits<double>::epsilon()*std::max(1.,squared(nodes[0].bounds.hi-nodes[0].bounds.lo));
 }
 Hit closest(Vec p)const{
  if(!finite(p))throw std::runtime_error("Non-finite nearest-point query");
  Hit best;double bestBary=0;bool bestDegenerate=false;
  std::vector<int> stack{0};
  while(!stack.empty()){
   int id=stack.back();stack.pop_back();const auto& node=nodes[id];
   if(node.bounds.distance2(p)>best.distance2+guard)continue;
   if(node.left<0){
    for(int i=node.begin;i<node.end;i++){
     const auto& t=triangles[i];auto q=triangle(p,t);double d=squared(p-q.p);
     if(d<best.distance2-guard){best={q.p,t.face,d,false};bestBary=q.minBary;bestDegenerate=q.degenerate;}
     else if(std::abs(d-best.distance2)<=guard){
      // Adjacent faces, coincident surfaces, or numerically tied candidates
      // keep the original Maya nearest-point ordering through fallback.
      if(t.face!=best.face||squared(q.p-best.p)>guard)best.ambiguous=true;
      if(d<best.distance2){bool tie=best.ambiguous;best={q.p,t.face,d,tie};bestBary=q.minBary;bestDegenerate=q.degenerate;}
     }
    }
   }else{
    double dl=nodes[node.left].bounds.distance2(p),dr=nodes[node.right].bounds.distance2(p);
    if(dl<dr){stack.push_back(node.right);stack.push_back(node.left);}else{stack.push_back(node.left);stack.push_back(node.right);}
   }
  }
  best.ambiguous=best.ambiguous||bestDegenerate||bestBary<=1e-8||best.face<0;
  return best;
 }
};
}
