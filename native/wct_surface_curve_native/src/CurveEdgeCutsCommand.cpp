#include "ProjectPointsCommand.h"
#include "ProjectPointsProtocol.h"
#include "CurveEdgeCutsCommand.h"
#include "StrictBoundaryQueryCommand.h"
#include "StrictQueryProtocol.h"
#include "CutProbes.h"
#include "ClosestQueryCompat.h"
#include "DoubleMeshBVH.h"
#include "FaceBoundsBVH.h"
#include "ExactPointCache.h"
#include <maya/MFnMeshData.h>
#include <maya/MMatrix.h>
#include <chrono>
#include <cstdlib>
#ifdef _WIN32
#include <windows.h>
#endif
#include <string>
#include <iomanip>
#include <maya/MArgList.h>
#include <maya/MFnMesh.h>
#include <maya/MFnNurbsCurve.h>
#include <maya/MSelectionList.h>
#include <maya/MDagPath.h>
#include <maya/MPointArray.h>
#include <maya/MIntArray.h>
#include <maya/MDoubleArray.h>
#include <maya/MGlobal.h>
#include <map>
#include <array>
#include <set>
#include <vector>
#include <sstream>
#include <locale>
#include <memory>
#include <stdexcept>
#include <iterator>
using wctcuts::Eval;
namespace {
using IDs=std::set<int>;
using Clock=std::chrono::steady_clock;
bool enabled(const char* name){
#ifdef _WIN32
 char value[8]={};DWORD n=GetEnvironmentVariableA(name,value,8);
 return n==1&&value[0]=='1';
#else
 const char* value=std::getenv(name);return value&&std::string(value)=="1";
#endif
}
double seconds(Clock::time_point start){return std::chrono::duration<double>(Clock::now()-start).count();}
struct Sample{MPoint p;int face;};
bool intersects(const IDs&a,const IDs&b){for(int x:a)if(b.count(x))return true;return false;}
MDagPath path(const MString& name){MSelectionList s;MDagPath p;if(!s.add(name)||!s.getDagPath(0,p))throw std::runtime_error("Missing DAG input");if(p.hasFn(MFn::kTransform))p.extendToShape();return p;}
void check(MStatus s,const char* message){if(!s)throw std::runtime_error(message);}
struct Surface {
 MFnMesh mesh;
 MMeshIsectAccelParams accelerator;
 bool pointAccelerated=false,normalAccelerated=false;
 bool usePointAcceleration=enabled("WCT_NATIVE_ACCEL_POINT");
 bool useDoubleBVH=enabled("WCT_NATIVE_BVH_POINT");
 bool useFaceBVH=enabled("WCT_NATIVE_FACE_QUERY");
 struct LocalFace {MObject owner;std::unique_ptr<MFnMesh> fn;};
 std::unique_ptr<wctface::BVH> faceBVH;
 std::map<int,LocalFace> faceMeshes;
 std::vector<std::vector<int>> faceVertexIds;
 MPointArray objectPoints;MMatrix worldMatrix,inverseMatrix;
 unsigned long faceHits=0,faceFallbacks=0,faceQueries=0;
 double faceBuildSeconds=0;
 std::unique_ptr<wctbvh::BVH> bvh;
 unsigned long bvhHits=0,bvhFallbacks=0;
 double bvhBuildSeconds=0;
 bool useNormalAcceleration=enabled("WCT_NATIVE_ACCEL_NORMAL");
 double curveSeconds=0,pointSeconds=0,normalSeconds=0,tangentSeconds=0;
 unsigned long curveCalls=0,pointCalls=0,normalCalls=0,tangentCalls=0;
 std::map<int,IDs> polygons;
 std::map<std::array<double,3>,MVector> normalCache;
 std::vector<IDs> graph;
 MPointArray points;
 bool useQueryReuse=false;int previousFace=-1;unsigned long pointMemoHits=0;
 struct ProjectedPoint{MPoint point;int face;};
 wctreuse::PointCache<ProjectedPoint> pointMemo;
 std::map<std::pair<int,int>,IDs> ringMemo;
 std::size_t ringMemoVertices=0;unsigned long ringMemoHits=0,ringMemoMisses=0;
 explicit Surface(const MDagPath& p,int strictMode=-1,bool queryReuse=false):mesh(p),accelerator(MFnMesh::autoUniformGridParams()){
  useQueryReuse=queryReuse&&strictMode<0;
  if(strictMode>=0){
   usePointAcceleration=false;useDoubleBVH=false;
   useNormalAcceleration=(strictMode&1)!=0;useFaceBVH=(strictMode&2)!=0;
  }
  check(mesh.getPoints(points,MSpace::kWorld),"Cannot read mesh points");
  // A skin deformation may leave a previously built accelerator stale.
  // Rebuild once per command, then reuse it for all queries in this batch.
  check(mesh.freeCachedIntersectionAccelerator(),"Cannot invalidate mesh accelerator");
  worldMatrix=p.inclusiveMatrix();inverseMatrix=p.inclusiveMatrixInverse();
  // A separate local mesh is queried in object space. Only use this route
  // for similarity transforms, where object/world distance ordering agrees.
  double scale2=0;bool similarity=true;
  for(int i=0;i<3;i++){
   double norm=0;for(int k=0;k<3;k++)norm+=worldMatrix[i][k]*worldMatrix[i][k];
   if(i==0)scale2=norm;else if(std::abs(norm-scale2)>1e-12*std::max(1.,scale2))similarity=false;
   for(int j=0;j<i;j++){double d=0;for(int k=0;k<3;k++)d+=worldMatrix[i][k]*worldMatrix[j][k];if(std::abs(d)>1e-12*std::max(1.,scale2))similarity=false;}
  }
  if(!(scale2>1e-24))similarity=false;
  useFaceBVH=useFaceBVH&&similarity;
  if(useFaceBVH){
   auto started=Clock::now();check(mesh.getPoints(objectPoints,MSpace::kObject),"Cannot read object points");
   MIntArray counts,vertices;check(mesh.getVertices(counts,vertices),"Cannot read polygons");
   faceVertexIds.resize(counts.length());std::vector<wctface::Item> faces;unsigned offset=0;
   for(unsigned face=0;face<counts.length();face++){
    wctface::Item item;item.face=int(face);
    for(int k=0;k<counts[face];k++){int v=vertices[offset++];faceVertexIds[face].push_back(v);auto q=objectPoints[v];item.bounds.add({q.x,q.y,q.z});}
    faces.push_back(item);
   }
   faceBVH=std::make_unique<wctface::BVH>(std::move(faces),useQueryReuse);faceBuildSeconds=seconds(started);
  }
  if(useDoubleBVH){
   auto started=Clock::now();MIntArray counts,vertices;
   check(mesh.getTriangles(counts,vertices),"Cannot read mesh triangulation");
   std::vector<wctbvh::Tri> tris;unsigned offset=0;
   for(unsigned face=0;face<counts.length();face++)for(int i=0;i<counts[face];i++){
    wctbvh::Tri t;t.face=int(face);wctbvh::Vec* dst[]={&t.a,&t.b,&t.c};
    for(int k=0;k<3;k++){if(offset>=vertices.length())throw std::runtime_error("Invalid triangle buffer");int v=vertices[offset++];if(v<0||v>=int(points.length()))throw std::runtime_error("Invalid triangle vertex");auto p=points[v];*dst[k]={p.x,p.y,p.z};}
    tris.push_back(t);
   }
   if(offset!=vertices.length())throw std::runtime_error("Unexpected triangle buffer tail");
   bvh=std::make_unique<wctbvh::BVH>(std::move(tris));bvhBuildSeconds=seconds(started);
  }
 }
 wctbvh::Vec projectFace(int face,const MPoint& query){
  ++faceQueries;auto found=faceMeshes.find(face);
  if(found==faceMeshes.end()){
   const auto& ids=faceVertexIds.at(face);if(ids.size()<3)throw std::runtime_error("Degenerate candidate polygon");
   MPointArray vertices;MIntArray counts,connects;counts.append(int(ids.size()));
   for(unsigned i=0;i<ids.size();i++){vertices.append(objectPoints[ids[i]]);connects.append(int(i));}
   MStatus s;MFnMeshData data;LocalFace entry;entry.owner=data.create(&s);check(s,"Cannot create candidate mesh data");
   entry.fn=std::make_unique<MFnMesh>();entry.fn->create(vertices.length(),1,vertices,counts,connects,entry.owner,&s);check(s,"Cannot build candidate polygon");
   found=faceMeshes.emplace(face,std::move(entry)).first;
  }
  MPoint closest;check(found->second.fn->getClosestPoint(query,closest,MSpace::kObject),"Candidate polygon projection failed");
  return {closest.x,closest.y,closest.z};
 }
 MStatus projectFaces(const MPoint& query,MPoint& closest,int* face){
  if(useQueryReuse){
   const auto* saved=pointMemo.find(query.x,query.y,query.z);
   if(saved){++pointMemoHits;closest=saved->point;*face=saved->face;previousFace=*face;return MS::kSuccess;}
  }
  auto finish=[&](MStatus status){
   if(status&&useQueryReuse){previousFace=*face;pointMemo.insert(query.x,query.y,query.z,{closest,*face});}
   return status;
  };
  MPoint objectQuery=query*inverseMatrix;
  try{
   auto project=[&](int id){return projectFace(id,objectQuery);};
   auto hit=useQueryReuse?faceBVH->closestOrdered({objectQuery.x,objectQuery.y,objectQuery.z},project,previousFace)
                         :faceBVH->closest({objectQuery.x,objectQuery.y,objectQuery.z},project);
   if(!hit.ambiguous){++faceHits;closest=MPoint(hit.p.x,hit.p.y,hit.p.z)*worldMatrix;*face=hit.face;return finish(MS::kSuccess);}
  }catch(const std::exception&){/* uncertain candidate projection: exact global fallback */}
  ++faceFallbacks;return finish(mesh.getClosestPoint(query,closest,MSpace::kWorld,face));
 }
 const IDs& polygon(int face){auto it=polygons.find(face);if(it!=polygons.end())return it->second;MIntArray vs;check(mesh.getPolygonVertices(face,vs),"Cannot read polygon");IDs ids;for(unsigned i=0;i<vs.length();i++)ids.insert(vs[i]);return polygons.emplace(face,std::move(ids)).first->second;}
 MVector normal(const MPoint& p){std::array<double,3> key{p.x,p.y,p.z};auto it=normalCache.find(key);if(it!=normalCache.end())return it->second;MVector n;auto started=Clock::now();
  MStatus status;
  if(useNormalAcceleration)status=wctquery::normal(mesh,p,n,MSpace::kWorld,&accelerator,normalAccelerated,0);
  else status=mesh.getClosestNormal(p,n,MSpace::kWorld);
  normalSeconds+=seconds(started);++normalCalls;check(status,"Mesh normal failed");normalCache.emplace(key,n);return n;}
 IDs expandOriginal(IDs out,int rings){IDs frontier=out;for(int k=0;k<rings;k++){IDs next;for(int v:frontier)for(int x:graph.at(v))if(!out.count(x))next.insert(x);out.insert(next.begin(),next.end());frontier=std::move(next);}return out;}
 const IDs& vertexRing(int vertex,int rings,IDs& scratch){
  auto key=std::make_pair(vertex,rings);auto found=ringMemo.find(key);
  if(found!=ringMemo.end()){++ringMemoHits;return found->second;}
  ++ringMemoMisses;scratch=expandOriginal({vertex},rings);
  if(ringMemo.size()<1024&&ringMemoVertices+scratch.size()<=32768){
   ringMemoVertices+=scratch.size();return ringMemo.emplace(key,std::move(scratch)).first->second;
  }
  return scratch;
 }
 IDs expand(IDs out,int rings){
  if(!useQueryReuse||out.empty()||out.size()>2)return expandOriginal(std::move(out),rings);
  auto vertex=out.begin();IDs scratchA;const IDs& a=vertexRing(*vertex,rings,scratchA);
  if(out.size()==1)return a;
  IDs scratchB;const IDs& b=vertexRing(*++vertex,rings,scratchB);IDs combined;
  std::set_union(a.begin(),a.end(),b.begin(),b.end(),std::inserter(combined,combined.end()));
  return combined;
 }
};
struct Curve {
 Surface& surface;MFnNurbsCurve curve;double eps;int rings;
 std::vector<Sample> samples;std::vector<IDs> sampleVertices;IDs nearVertices;
 std::map<int,Eval> endpoints;
 std::map<IDs,std::vector<int>> candidateCache;
 Curve(Surface&s,const MDagPath&p,double e,int r):surface(s),curve(p),eps(e),rings(r){}
 void prepare(){IDs touched;for(auto& s:samples){sampleVertices.push_back(surface.polygon(s.face));touched.insert(sampleVertices.back().begin(),sampleVertices.back().end());}nearVertices=surface.expand(touched,1);}
 Eval evaluate(const MPoint& point,const IDs& local){
  MStatus status;double param=0;
  auto started=Clock::now();
  MPoint cp=curve.closestPoint(point,&param,eps*.1,MSpace::kWorld,&status);
  surface.curveSeconds+=seconds(started);++surface.curveCalls;check(status,"Curve closestPoint failed");
  MPoint projected;int face=-1;started=Clock::now();
  if(surface.useFaceBVH)status=surface.projectFaces(cp,projected,&face);
  else if(surface.useDoubleBVH){
   auto hit=surface.bvh->closest({cp.x,cp.y,cp.z});
   if(hit.ambiguous){++surface.bvhFallbacks;status=surface.mesh.getClosestPoint(cp,projected,MSpace::kWorld,&face);}
   else{++surface.bvhHits;projected=MPoint(hit.p.x,hit.p.y,hit.p.z);face=hit.face;status=MS::kSuccess;}
  }
  else if(surface.usePointAcceleration)status=wctquery::closest(surface.mesh,cp,projected,MSpace::kWorld,&face,&surface.accelerator,surface.pointAccelerated,0);
  else status=surface.mesh.getClosestPoint(cp,projected,MSpace::kWorld,&face);
  surface.pointSeconds+=seconds(started);++surface.pointCalls;check(status,"Mesh closestPoint failed");cp=projected;
  MVector tangent,normal;
  if(intersects(surface.polygon(face),local)){
   started=Clock::now();tangent=curve.tangent(param,MSpace::kWorld,&status);
   surface.tangentSeconds+=seconds(started);++surface.tangentCalls;check(status,"Curve tangent failed");
   normal=surface.normal(cp);
  }else{
   auto found=candidateCache.find(local);
   if(found==candidateCache.end()){
    std::vector<int> candidates;for(int i=0;i<int(samples.size());i++)if(intersects(sampleVertices[i],local))candidates.push_back(i);
    found=candidateCache.emplace(local,std::move(candidates)).first;
   }
   const auto& candidates=found->second;if(candidates.empty())return {};
   int bestSample=candidates.front();double minDist=(point-samples[bestSample].p).length();
   for(int i:candidates){double d=(point-samples[i].p).length();if(d<minDist){minDist=d;bestSample=i;}}
   bool valid=false;double bestDistance=0;MPoint bestPoint;MVector bestTangent;
   int n=int(samples.size());
   for(auto pair:{std::make_pair((bestSample+n-1)%n,bestSample),std::make_pair(bestSample,(bestSample+1)%n)}){
    int a=pair.first,b=pair.second;if(!intersects(sampleVertices[a],local)||!intersects(sampleVertices[b],local))continue;
    MVector delta=samples[b].p-samples[a].p;double length2=delta*delta;if(length2<=eps*eps)continue;
    double u=std::clamp(((point-samples[a].p)*delta)/length2,0.,1.);MPoint q=samples[a].p+delta*u;double d=(point-q).length();
    if(!valid||d<bestDistance){valid=true;bestDistance=d;bestPoint=q;bestTangent=delta;}
   }
   if(!valid)return {};cp=bestPoint;tangent=bestTangent;
   normal=surface.normal(samples[bestSample].p);
  }
  MVector axis=normal^tangent;double n=axis.length();if(n<=1e-12)return {};
  return {true,((point-cp)*axis)/n,(point-cp).length()};
 }
 Eval endpoint(int vertex){auto it=endpoints.find(vertex);if(it!=endpoints.end())return it->second;return endpoints.emplace(vertex,evaluate(surface.points[vertex],surface.expand({vertex},rings))).first->second;}
};
}
MStatus WctCurveEdgeCuts::doIt(const MArgList& args){
 try{
  if(args.length()!=8&&args.length()!=9)throw std::runtime_error("Expected mesh, ridge, fwd, bwd, eps, median, rings, payload[, reuse]");
  int queryReuse=args.length()==9?args.asInt(8):0;
  if(queryReuse<0||queryReuse>1)throw std::runtime_error("Query reuse must be 0 or 1");
  double eps=args.asDouble(4),median=args.asDouble(5);int rings=args.asInt(6);
  if(!std::isfinite(eps)||eps<=0||!std::isfinite(median)||median<=0||rings<0||rings>16)throw std::runtime_error("Invalid tolerances");
  Surface surface(path(args.asString(0)),-1,queryReuse==1);std::istringstream input(args.asString(7).asChar());input.imbue(std::locale::classic());
  int version,count,edgeCount,selectedMask=7;if(!(input>>version>>count>>edgeCount)||(version!=1&&version!=2)||count!=int(surface.points.length())||edgeCount<0||edgeCount>10000000)throw std::runtime_error("Invalid payload header");
  if(version==2&&(!(input>>selectedMask)||selectedMask<0||selectedMask>7))throw std::runtime_error("Invalid curve selection mask");
  surface.graph.resize(count);std::vector<std::pair<int,int>> edges;std::vector<double> lengths;
  for(int i=0;i<edgeCount;i++){int a,b;double length;if(!(input>>a>>b>>length)||a<0||b<=a||b>=count||!std::isfinite(length)||length<=0)throw std::runtime_error("Invalid edge");edges.push_back({a,b});lengths.push_back(length);surface.graph[a].insert(b);surface.graph[b].insert(a);}
  std::vector<std::unique_ptr<Curve>> curves;
  for(int ci=0;ci<3;ci++){
   curves.push_back(std::make_unique<Curve>(surface,path(args.asString(ci+1)),eps,rings));
   int n;if(!(input>>n)||n<1||n>1000000)throw std::runtime_error("Invalid sample count");
   for(int i=0;i<n;i++){double x,y,z;int f;if(!(input>>x>>y>>z>>f)||!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(z)||f<0||f>=surface.mesh.numPolygons())throw std::runtime_error("Invalid sample");curves.back()->samples.push_back({MPoint(x,y,z),f});}
   curves.back()->prepare();
  }
  std::string trailing;if(input>>trailing)throw std::runtime_error("Unexpected payload trailer");
  MDoubleArray result;
  auto record=[&](double t,double a,double b,double c,double d,double e){for(double x:{t,a,b,c,d,e})result.append(x);};
  const double values[]={.5,1.,0.};
  for(int ei=0;ei<edgeCount;ei++){
   int a=edges[ei].first,b=edges[ei].second;MPoint pa=surface.points[a],pb=surface.points[b];MVector delta=pb-pa;double length=lengths[ei];
   bool relevant=false;for(int ci=0;ci<3;ci++)if((selectedMask&(1<<ci))&&(curves[ci]->nearVertices.count(a)||curves[ci]->nearVertices.count(b))){relevant=true;break;}
   if(!relevant)continue;
   IDs local=surface.expand({a,b},rings);
   for(int ci=0;ci<3;ci++){
    if(!(selectedMask&(1<<ci)))continue;
    auto& c=*curves[ci];if(!c.nearVertices.count(a)&&!c.nearVertices.count(b))continue;
    Eval ra=c.endpoint(a),rb=c.endpoint(b);
    auto cuts=wctcuts::cuts(ra,rb,length,median,eps,[&](double u){return c.evaluate(pa+delta*u,local);});
    for(double u:cuts)record(0,ei,u,values[ci],0,0);
   }
  }
  for(int ci=0;ci<3;ci++)for(auto& item:curves[ci]->endpoints){auto r=item.second;record(1,ci,item.first,r.valid?1:0,r.side,r.distance);}
  record(2,1,edgeCount,count,0,0);
  std::ostringstream message;message.imbue(std::locale::classic());message<<std::fixed<<std::setprecision(3)
   <<"[WCT Native Query] recompute_mask="<<selectedMask<<" curve="<<surface.curveSeconds<<"s/"<<surface.curveCalls
   <<" mesh="<<surface.pointSeconds<<"s/"<<surface.pointCalls
   <<" normal="<<surface.normalSeconds<<"s/"<<surface.normalCalls
   <<" tangent="<<surface.tangentSeconds<<"s/"<<surface.tangentCalls
   <<" accel_point="<<surface.pointAccelerated<<" accel_normal="<<surface.normalAccelerated
   <<" face_bvh="<<surface.useFaceBVH<<" face_hits="<<surface.faceHits<<" face_fallbacks="<<surface.faceFallbacks<<" face_queries="<<surface.faceQueries<<" face_meshes="<<surface.faceMeshes.size()<<" face_build="<<surface.faceBuildSeconds<<"s"
   <<" double_bvh="<<surface.useDoubleBVH<<" bvh_hits="<<surface.bvhHits<<" bvh_fallbacks="<<surface.bvhFallbacks<<" bvh_build="<<surface.bvhBuildSeconds<<"s";
  message<<" query_reuse="<<surface.useQueryReuse<<" point_memo_hits="<<surface.pointMemoHits<<" point_memo_size="<<surface.pointMemo.size()
         <<" ring_memo_hits="<<surface.ringMemoHits<<" ring_memo_misses="<<surface.ringMemoMisses;
  MGlobal::displayInfo(MString(message.str().c_str()));
  setResult(result);return MS::kSuccess;
 }catch(const std::exception&e){MGlobal::displayError(MString("wctCurveEdgeCuts: ")+e.what());return MS::kFailure;}
}

MStatus WctStrictBoundaryQueries::doIt(const MArgList& args){
 try{
  if(args.length()!=6)throw std::runtime_error("Expected mesh, curve, eps, rings, mode, payload");
  double eps=args.asDouble(2);int rings=args.asInt(3),mode=args.asInt(4);
  if(!std::isfinite(eps)||eps<=0||rings<0||rings>16||mode<0||mode>3)
   throw std::runtime_error("Invalid strict-query tolerances/mode");
  auto started=Clock::now();Surface surface(path(args.asString(0)),mode);
  MFnNurbsCurve curve(path(args.asString(1)));
  std::istringstream input(args.asString(5).asChar());input.imbue(std::locale::classic());
  auto request=wctstrict::parse(input,int(surface.points.length()));
  surface.graph.resize(request.count);
  for(auto edge:request.edges){surface.graph[edge.first].insert(edge.second);surface.graph[edge.second].insert(edge.first);}
  MDoubleArray result;
  for(int vertex:request.vertices){
   MStatus status;double parameter=0;auto point=surface.points[vertex];auto stage=Clock::now();
   auto cp=curve.closestPoint(point,&parameter,eps*.1,MSpace::kWorld,&status);
   surface.curveSeconds+=seconds(stage);++surface.curveCalls;check(status,"Strict curve closestPoint failed");
   MPoint projected;int face=-1;stage=Clock::now();
   if(surface.useFaceBVH)status=surface.projectFaces(cp,projected,&face);
   else status=surface.mesh.getClosestPoint(cp,projected,MSpace::kWorld,&face);
   surface.pointSeconds+=seconds(stage);++surface.pointCalls;check(status,"Strict mesh closestPoint failed");
   bool valid=false;double value=0;
   if(intersects(surface.polygon(face),surface.expand({vertex},rings))){
    // Match Python strict refinement exactly: normal before tangent, no
    // sampled-curve fallback and no distance-field endpoint approximation.
    auto normal=surface.normal(projected);stage=Clock::now();
    auto tangent=curve.tangent(parameter,MSpace::kWorld,&status);
    surface.tangentSeconds+=seconds(stage);++surface.tangentCalls;check(status,"Strict curve tangent failed");
    auto axis=normal^tangent;double length=axis.length();
    if(!(length<1e-12)){
     value=((point-projected)*axis)/length;
     if(!std::isfinite(value))throw std::runtime_error("Non-finite strict-query value");
     valid=true;
    }
   }
   result.append(vertex);result.append(valid?1.:0.);result.append(value);
  }
  result.append(-1.);result.append(1.);result.append(double(request.vertices.size()));
  std::ostringstream message;message.imbue(std::locale::classic());message<<std::fixed<<std::setprecision(4)
   <<"[WCT Native Strict] mode="<<mode<<" vertices="<<request.vertices.size()<<" total="<<seconds(started)
   <<"s curve="<<surface.curveSeconds<<"s mesh="<<surface.pointSeconds<<"s normal="<<surface.normalSeconds
   <<"s tangent="<<surface.tangentSeconds<<"s face_hits="<<surface.faceHits<<" fallbacks="<<surface.faceFallbacks;
  MGlobal::displayInfo(MString(message.str().c_str()));setResult(result);return MS::kSuccess;
 }catch(const std::exception& e){MGlobal::displayError(MString("wctStrictBoundaryQueries: ")+e.what());return MS::kFailure;}
}

MStatus WctProjectPoints::doIt(const MArgList& args){
 try{
  if(args.length()!=3)throw std::runtime_error("Expected mesh, mode, points payload");
  int mode=args.asInt(1);if(mode!=0&&mode!=2)throw std::runtime_error("Projection mode must be 0 or 2");
  std::istringstream input(args.asString(2).asChar());input.imbue(std::locale::classic());
  auto points=wctproject::parse(input);auto started=Clock::now();
  Surface surface(path(args.asString(0)),mode);MDoubleArray result;
  for(const auto& xyz:points){
   MPoint query(xyz[0],xyz[1],xyz[2]),closest;int face=-1;MStatus status;
   if(surface.useFaceBVH)status=surface.projectFaces(query,closest,&face);
   else status=surface.mesh.getClosestPoint(query,closest,MSpace::kWorld,&face);
   check(status,"Project-point query failed");
   if(face<0||!std::isfinite(closest.x)||!std::isfinite(closest.y)||!std::isfinite(closest.z))
    throw std::runtime_error("Invalid projected point");
   result.append(closest.x);result.append(closest.y);result.append(closest.z);result.append(face);
  }
  result.append(-1.);result.append(1.);result.append(double(points.size()));result.append(-2.);
  std::ostringstream message;message.imbue(std::locale::classic());message<<std::fixed<<std::setprecision(4)
   <<"[WCT Native Project] mode="<<mode<<" points="<<points.size()<<" total="<<seconds(started)
   <<"s face_hits="<<surface.faceHits<<" fallbacks="<<surface.faceFallbacks;
  MGlobal::displayInfo(MString(message.str().c_str()));setResult(result);return MS::kSuccess;
 }catch(const std::exception& e){MGlobal::displayError(MString("wctProjectPoints: ")+e.what());return MS::kFailure;}
}
