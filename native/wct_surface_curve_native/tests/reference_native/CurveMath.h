#pragma once
// Dependency-free numerical kernel; all coordinates use one common space.
#include <vector>
#include <cmath>
#include <algorithm>
#include <stdexcept>
namespace wct {
struct V {
 double x=0,y=0,z=0;
 V()=default; V(double a,double b,double c):x(a),y(b),z(c){}
 V operator+(V b)const{return {x+b.x,y+b.y,z+b.z};}
 V operator-(V b)const{return {x-b.x,y-b.y,z-b.z};}
 V operator*(double a)const{return {x*a,y*a,z*a};}
 V operator/(double a)const{return *this*(1/a);}
};
inline double dot(V a,V b){return a.x*b.x+a.y*b.y+a.z*b.z;}
inline V cross(V a,V b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
inline double norm(V a){return std::sqrt(dot(a,a));}
inline bool finite(V p){return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z);}
inline V unit(V a){double n=norm(a);return n>1e-9?a/n:V{};}
inline bool circle(V a,V b,V c,V& center,double& radius){
 V u=b-a,v=c-a,n=cross(u,v); double d=dot(n,n);
 if(d<=1e-16*std::max(dot(u,u)*dot(v,v),1e-24))return false;
 center=a+(cross(v,n)*dot(u,u)+cross(n,u)*dot(v,v))/(2*d);
 radius=norm(a-center);return radius*radius>1e-12;
}
inline std::vector<V> skeleton(const std::vector<V>& a,bool closed,int samples){
 if(a.size()<2||samples<1)throw std::runtime_error("At least two anchors and positive samples required");
 for(auto p:a)if(!finite(p))throw std::runtime_error("Non-finite anchor");
 int n=int(a.size()); if(closed&&n<3)throw std::runtime_error("Closed curves require three anchors");
 std::vector<V> dirs(n),out;
 for(int i=0;i<n;i++){
  if(!closed&&i==0){dirs[i]=unit(a[1]-a[0]);continue;}
  if(!closed&&i==n-1){dirs[i]=unit(a[i]-a[i-1]);continue;}
  V prev=a[(i+n-1)%n],next=a[(i+1)%n],o;double r;
  if(circle(prev,a[i],next,o,r)){
   V t=cross(unit(cross(a[i]-prev,next-prev)),a[i]-o);
   if(dot(t,next-a[i])<0)t=t*(-1);dirs[i]=unit(t);
  }else dirs[i]=unit(next-prev);
 }
 int segments=closed?n:n-1;
 for(int i=0;i<segments;i++){
  V p=a[i],q=a[(i+1)%n],o;double r,L=norm(q-p);
  int helper=closed?(i+2)%n:(i+2<n?i+2:i-1);
  if(helper>=0&&circle(p,q,a[helper],o,r)){
   V u=p-o,v=q-o;double den=norm(u)*norm(v);
   double theta=den>1e-18?std::acos(std::clamp(dot(u,v)/den,-1.,1.)):0.;
   if(r>1e-9&&theta>1e-6)L=4*std::tan(theta/4)*r;
  }
  for(int j=0;j<samples;j++){
   double t=double(j)/samples,t2=t*t,t3=t2*t;
   out.push_back(p*(2*t3-3*t2+1)+dirs[i]*(L*(t3-2*t2+t))+q*(-2*t3+3*t2)+dirs[(i+1)%n]*(L*(t3-t2)));
  }
 }
 out.push_back(closed?a[0]:a.back());return out;
}
inline void laplacian(std::vector<V>& p,int samples,int iterations,double weight){
 auto next=p;
 for(int k=0;k<iterations;k++){
  for(size_t i=1;i+1<p.size();i++)if(i%size_t(samples)!=0)next[i]=p[i]*(1-weight)+(p[i-1]+p[i+1])*(.5*weight);
  p.swap(next);
 }
}
inline std::vector<V> gaussian(const std::vector<V>& delta,int samples,bool closed,double sigma,int radius){
 auto out=delta;int n=int(delta.size());
 for(int i=0;i<n;i++){
  if(i%samples==0){out[i]={};continue;}
  V sum;double total=0;
  for(int k=-radius;k<=radius;k++){
   int j=i+k;if(closed)j=(j%n+n)%n;else if(j<0||j>=n)continue;
   double w=std::exp(-.5*k*k/(sigma*sigma));sum=sum+delta[j]*w;total+=w;
  }out[i]=sum/total;
 }return out;
}
inline std::vector<V> sample(const std::vector<V>& p,int begin,int end,int intervals){
 if(intervals<1||begin<0||end<begin||end>=int(p.size()))throw std::runtime_error("Invalid resampling interval");
 std::vector<double> arc(end-begin+1,0);for(int j=begin+1;j<=end;j++)arc[j-begin]=arc[j-begin-1]+norm(p[j]-p[j-1]);
 std::vector<V> out;size_t j=1;
 for(int i=0;i<=intervals;i++){
  double d=arc.back()*i/intervals;
  if(arc.back()<1e-12){out.push_back(p[begin]);continue;}
  while(j+1<arc.size()&&arc[j]<d)++j;
  double denom=arc[j]-arc[j-1],t=denom>1e-12?(d-arc[j-1])/denom:0;
  out.push_back(p[begin+int(j)-1]*(1-t)+p[begin+int(j)]*t);
 }return out;
}
inline std::vector<V> resample(const std::vector<V>& dense,int samples,const std::vector<int>& ids,int count,bool closed){
 int n=int(ids.size()),segments=closed?n:n-1;
 if(n<2||count<2||int(dense.size())!=segments*samples+1)throw std::runtime_error("Invalid EP layout");
 for(int i=0;i<n;i++)if(ids[i]<0||ids[i]>=count||(i&&ids[i]<=ids[i-1]))throw std::runtime_error("EP indices must be strictly increasing");
 if(!closed&&(ids.front()!=0||ids.back()!=count-1))throw std::runtime_error("Open EP layout must include endpoints");
 std::vector<V> out(count);
 for(int i=0;i<segments;i++){
  int steps=closed?(ids[(i+1)%n]-ids[i]+count)%count:ids[i+1]-ids[i];
  auto seg=sample(dense,i*samples,(i+1)*samples,steps);
  for(int j=0;j<=steps;j++)out[(ids[i]+j)%count]=seg[j];
 }return out;
}
// Solve periodic cubic interpolation, then rotate for Maya's knot domain [0,n].
inline std::vector<V> periodicCV(const std::vector<V>& ep){
 int n=int(ep.size());if(n<4)throw std::runtime_error("Periodic cubic requires four EPs");
 std::vector<std::vector<double>> A(n,std::vector<double>(n));auto b=ep;
 for(int i=0;i<n;i++){A[i][i]=4;A[i][(i+n-1)%n]+=1;A[i][(i+1)%n]+=1;b[i]=b[i]*6;}
 for(int i=0;i<n;i++){
  double pivot=A[i][i];for(int j=i;j<n;j++)A[i][j]/=pivot;b[i]=b[i]/pivot;
  for(int k=i+1;k<n;k++){double f=A[k][i];for(int j=i;j<n;j++)A[k][j]-=f*A[i][j];b[k]=b[k]-b[i]*f;}
 }
 for(int i=n-1;i>=0;i--)for(int j=i+1;j<n;j++)b[i]=b[i]-b[j]*A[i][j];
 std::vector<V> out;for(int i=0;i<n+3;i++)out.push_back(b[(i+n-1)%n]);return out;
}
}
