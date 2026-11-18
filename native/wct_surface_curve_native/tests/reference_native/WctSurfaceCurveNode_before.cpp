#include "CurveMath.h"
#include "node_ids.h"
#include "CurveEdgeCutsCommand.h"
#include <maya/MPxNode.h>
#include <maya/MPlug.h>
#include <maya/MIntArray.h>
#include <maya/MTypeId.h>
#include <maya/MFnPlugin.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnMesh.h>
#include <maya/MMeshIntersector.h>
#include <maya/MFnNurbsCurve.h>
#include <maya/MFnNurbsCurveData.h>
#include <maya/MFnPointArrayData.h>
#include <maya/MFnIntArrayData.h>
#include <maya/MArrayDataHandle.h>
#include <maya/MDataBlock.h>
#include <maya/MDataHandle.h>
#include <maya/MPointArray.h>
#include <maya/MDoubleArray.h>
#include <maya/MGlobal.h>
#include <memory>
using wct::V;
static V vec(const MPoint& p){return {p.x,p.y,p.z};}
static MPoint point(V p){return {p.x,p.y,p.z};}
class WctSurfaceCurve : public MPxNode {
public:
 static MObject referenceMesh,displayMesh,anchors,id0,id1,id2,u,v,w;
 static MObject closed,samples,iterations,weight,gaussianMode,sigma,gaussianRadius,adhesion;
 static MObject epIndices,epCount,surfaceOffset,outputCurve,outputLocalPoints;
 static void* creator(){return new WctSurfaceCurve;}
 static MStatus initialize();
 SchedulingType schedulingType()const override{return kSerial;}
 MStatus compute(const MPlug&,MDataBlock&) override;
private:
 std::unique_ptr<MMeshIntersector> intersector;
 MPointArray cachedPoints; MIntArray cachedCounts,cachedConnects;
 // The datablock retains this immutable input snapshot during the drag.
 MObject cachedMesh;
 void prepare(MObject mesh);
 V project(V p){MPointOnMesh hit;MStatus s=intersector->getClosestPoint(point(p),hit);if(!s)throw std::runtime_error("Reference surface projection failed");auto q=hit.getPoint();return {q.x,q.y,q.z};}
};
#define OBJ(name) MObject WctSurfaceCurve::name;
OBJ(referenceMesh) OBJ(displayMesh) OBJ(anchors) OBJ(id0) OBJ(id1) OBJ(id2) OBJ(u) OBJ(v) OBJ(w)
OBJ(closed) OBJ(samples) OBJ(iterations) OBJ(weight) OBJ(gaussianMode) OBJ(sigma) OBJ(gaussianRadius) OBJ(adhesion)
OBJ(epIndices) OBJ(epCount) OBJ(surfaceOffset) OBJ(outputCurve) OBJ(outputLocalPoints)
#undef OBJ
MStatus WctSurfaceCurve::initialize(){
 MFnNumericAttribute n;MFnTypedAttribute t;MFnCompoundAttribute c;MStatus s;
 std::vector<MObject> inputs;
 auto numeric=[&](const char* name,const char* shortName,MFnNumericData::Type type,double value){
  MObject a=n.create(name,shortName,type,value,&s);if(!s)return MObject::kNullObj;
  n.setKeyable(true);addAttribute(a);inputs.push_back(a);return a;
 };
 referenceMesh=t.create("referenceMesh","rm",MFnData::kMesh);t.setStorable(false);addAttribute(referenceMesh);inputs.push_back(referenceMesh);
 displayMesh=t.create("displayMesh","dm",MFnData::kMesh);t.setStorable(false);addAttribute(displayMesh);inputs.push_back(displayMesh);
 id0=n.create("vertex0","v0",MFnNumericData::kInt,0);id1=n.create("vertex1","v1",MFnNumericData::kInt,0);id2=n.create("vertex2","v2",MFnNumericData::kInt,0);
 u=n.create("weight0","w0",MFnNumericData::kDouble,1.);v=n.create("weight1","w1",MFnNumericData::kDouble,0.);w=n.create("weight2","w2",MFnNumericData::kDouble,0.);
 anchors=c.create("anchors","anc");for(auto a:{id0,id1,id2,u,v,w})c.addChild(a);
 c.setArray(true);c.setUsesArrayDataBuilder(true);addAttribute(anchors);inputs.push_back(anchors);
 closed=numeric("closed","cl",MFnNumericData::kBoolean,1);
 samples=numeric("samplesPerSegment","sps",MFnNumericData::kInt,60);n.setMin(2);n.setMax(256);
 iterations=numeric("iterations","it",MFnNumericData::kInt,12);n.setMin(0);n.setMax(100);
 weight=numeric("smoothWeight","sw",MFnNumericData::kDouble,.7);n.setMin(0.);n.setMax(1.);
 gaussianMode=numeric("gaussianMode","gm",MFnNumericData::kBoolean,0);
 sigma=numeric("gaussianSigma","gs",MFnNumericData::kDouble,6);n.setMin(.01);
 gaussianRadius=numeric("gaussianRadius","gr",MFnNumericData::kInt,15);n.setMin(1);n.setMax(100);
 adhesion=numeric("adhesion","ad",MFnNumericData::kDouble,.85);n.setMin(0.);n.setMax(1.);
 epCount=numeric("epCount","ec",MFnNumericData::kInt,12);n.setMin(2);n.setMax(512);
 surfaceOffset=numeric("surfaceOffset","so",MFnNumericData::kDouble,.02);n.setMin(0.);n.setMax(10.);
 epIndices=t.create("epIndices","ei",MFnData::kIntArray);addAttribute(epIndices);inputs.push_back(epIndices);
 outputCurve=t.create("outputCurve","oc",MFnData::kNurbsCurve);t.setWritable(false);t.setStorable(false);addAttribute(outputCurve);
 outputLocalPoints=t.create("outputLocalPoints","olp",MFnData::kPointArray);t.setWritable(false);t.setStorable(false);addAttribute(outputLocalPoints);
 for(auto a:inputs){attributeAffects(a,outputCurve);attributeAffects(a,outputLocalPoints);}
 for(auto a:{id0,id1,id2,u,v,w}){attributeAffects(a,outputCurve);attributeAffects(a,outputLocalPoints);}
 return MS::kSuccess;
}
void WctSurfaceCurve::prepare(MObject mesh){
 MStatus s;MFnMesh fn(mesh,&s);if(!s)throw std::runtime_error("Invalid reference mesh");
 MPointArray p;MIntArray counts,connects;
 fn.getPoints(p,MSpace::kObject);fn.getVertices(counts,connects);
 bool changed=!intersector||mesh!=cachedMesh||p.length()!=cachedPoints.length()||counts.length()!=cachedCounts.length()||connects.length()!=cachedConnects.length();
 if(!changed)for(unsigned i=0;i<p.length();i++)if(p[i]!=cachedPoints[i]){changed=true;break;}
 if(!changed)for(unsigned i=0;i<counts.length();i++)if(counts[i]!=cachedCounts[i]){changed=true;break;}
 if(!changed)for(unsigned i=0;i<connects.length();i++)if(connects[i]!=cachedConnects[i]){changed=true;break;}
 if(changed){
  auto candidate=std::make_unique<MMeshIntersector>();
  s=candidate->create(mesh);if(!s)throw std::runtime_error("Cannot build reference intersector");
  intersector=std::move(candidate);cachedMesh=mesh;cachedPoints=p;cachedCounts=counts;cachedConnects=connects;
 }
}
MStatus WctSurfaceCurve::compute(const MPlug& plug,MDataBlock& data){
 if(plug!=outputCurve&&plug!=outputLocalPoints)return MS::kUnknownParameter;
 try{
  MStatus s;MObject ref=data.inputValue(referenceMesh).asMesh();prepare(ref);
  MObject disp=data.inputValue(displayMesh).asMesh();MFnMesh displayFn(disp,&s);if(!s)throw std::runtime_error("Invalid display mesh");
  MPointArray displayPoints;displayFn.getPoints(displayPoints,MSpace::kObject);
  MIntArray dc,dv;displayFn.getVertices(dc,dv);
  if(dc.length()!=cachedCounts.length()||dv.length()!=cachedConnects.length())throw std::runtime_error("Reference/display topology differs");
  for(unsigned i=0;i<dc.length();i++)if(dc[i]!=cachedCounts[i])throw std::runtime_error("Reference/display polygon layout differs");
  for(unsigned i=0;i<dv.length();i++)if(dv[i]!=cachedConnects[i])throw std::runtime_error("Reference/display vertex ordering differs");
  if(displayPoints.length()!=cachedPoints.length())throw std::runtime_error("Reference/display vertex counts differ");
  MFnMesh refFn(ref);
  MArrayDataHandle array=data.inputArrayValue(anchors);unsigned count=array.elementCount();
  if(count<2||count>128)throw std::runtime_error("Expected 2..128 contiguous anchors");
  std::vector<V> raw;
  for(unsigned i=0;i<count;i++){
   if(!array.jumpToElement(i))throw std::runtime_error("Anchor indices must be contiguous from zero");
   auto h=array.inputValue();int ids[]={h.child(id0).asInt(),h.child(id1).asInt(),h.child(id2).asInt()};
   double weights[]={h.child(u).asDouble(),h.child(v).asDouble(),h.child(w).asDouble()};V p;double sum=0;
   for(int k=0;k<3;k++){
    if(ids[k]<0||ids[k]>=int(cachedPoints.length())||!std::isfinite(weights[k])||weights[k]<-1e-6)throw std::runtime_error("Invalid loc surface binding");
    p=p+vec(cachedPoints[ids[k]])*weights[k];sum+=weights[k];
   }if(std::abs(sum-1)>1e-4)throw std::runtime_error("Loc barycentric weights must sum to one");raw.push_back(p);
  }
  bool isClosed=data.inputValue(closed).asBool();int ns=data.inputValue(samples).asInt();
  int ni=data.inputValue(iterations).asInt(),nr=data.inputValue(gaussianRadius).asInt(),nc=data.inputValue(epCount).asInt();
  double sw=data.inputValue(weight).asDouble(),sig=data.inputValue(sigma).asDouble(),ad=data.inputValue(adhesion).asDouble(),off=data.inputValue(surfaceOffset).asDouble();
  if((isClosed&&nc<4)||ns<2||ns>256||ni<0||ni>100||nr<1||nr>100||nc<2||nc>512||!std::isfinite(sw)||sw<0||sw>1||!std::isfinite(sig)||sig<=0||!std::isfinite(ad)||ad<0||ad>1||!std::isfinite(off)||off<0)throw std::runtime_error("Invalid curve parameters");
  MObject indicesObject=data.inputValue(epIndices).data();MFnIntArrayData indicesData(indicesObject,&s);if(!s)throw std::runtime_error("Missing EP index layout");
  MIntArray indexArray=indicesData.array();if(indexArray.length()!=count)throw std::runtime_error("EP index count differs from anchors");
  std::vector<int> ids;for(unsigned i=0;i<count;i++)ids.push_back(indexArray[i]);
  auto dense=wct::skeleton(raw,isClosed,ns),projected=dense;
  for(auto& p:projected)p=project(p);
  if(data.inputValue(gaussianMode).asBool()){
   auto delta=projected;for(size_t i=0;i<delta.size();i++)delta[i]=i%ns?projected[i]-dense[i]:V{};
   auto filtered=wct::gaussian(delta,ns,isClosed,sig,nr);
   for(size_t i=0;i<dense.size();i++)dense[i]=dense[i]+filtered[i]*ad;
  }else{
   dense=std::move(projected);wct::laplacian(dense,ns,ni,sw);
   if(ni>0&&sw>0)for(auto& p:dense)p=project(p);
  }
  auto local=wct::resample(dense,ns,ids,nc,isClosed);
  // Matches the boundary algorithm: resample, project, restore exact anchors.
  for(auto& p:local)p=project(p);
  for(unsigned i=0;i<count;i++)local[ids[i]]=raw[i];
  MPointArray localArray,visual;
  for(auto p:local){
   localArray.append(point(p));MPointOnMesh hit;
   if(!intersector->getClosestPoint(point(p),hit))throw std::runtime_error("Display mapping projection failed");
   int tri[3];if(!refFn.getPolygonTriangleVertices(hit.faceIndex(),hit.triangleIndex(),tri))throw std::runtime_error("Cannot read mapping triangle");
   float a,b;hit.getBarycentricCoords(a,b);double c=1.-a-b;
   V q=vec(displayPoints[tri[0]])*a+vec(displayPoints[tri[1]])*b+vec(displayPoints[tri[2]])*c;
   V normal=wct::unit(wct::cross(vec(displayPoints[tri[1]])-vec(displayPoints[tri[0]]),vec(displayPoints[tri[2]])-vec(displayPoints[tri[0]])));
   auto hp=hit.getPoint();q=q+(p-V{hp.x,hp.y,hp.z})+normal*off;
   visual.append(point(q));
  }
  MFnNurbsCurveData curveData;MObject out=curveData.create();MFnNurbsCurve fn;
  if(isClosed){
   std::vector<V> ep;for(unsigned i=0;i<visual.length();i++)ep.push_back(vec(visual[i]));
   auto cvs=wct::periodicCV(ep);MPointArray cvArray;for(auto p:cvs)cvArray.append(point(p));
   MDoubleArray knots;for(int i=-2;i<=nc+2;i++)knots.append(i);
   fn.create(cvArray,knots,3,MFnNurbsCurve::kPeriodic,false,false,out,&s);
  }else fn.createWithEditPoints(visual,nc>=4?3:1,MFnNurbsCurve::kOpen,false,false,true,out,&s);
  if(!s)throw std::runtime_error("NURBS creation failed");
  MFnPointArrayData pointsData;MObject pointData=pointsData.create(localArray,&s);if(!s)throw std::runtime_error("Point array creation failed");
  auto curveHandle=data.outputValue(outputCurve);curveHandle.setMObject(out);curveHandle.setClean();
  auto pointHandle=data.outputValue(outputLocalPoints);pointHandle.setMObject(pointData);pointHandle.setClean();
  return MS::kSuccess;
 }catch(const std::exception& e){MGlobal::displayError(MString("wctSurfaceCurve: ")+e.what());return MS::kFailure;}
}
#ifdef _WIN32
#define WCT_EXPORT __declspec(dllexport)
#else
#define WCT_EXPORT
#endif
WCT_EXPORT MStatus initializePlugin(MObject obj){
 MFnPlugin p(obj,"WeightCurveTool","1.7.0","Any");
 MStatus s=p.registerCommand("wctCurveEdgeCuts",WctCurveEdgeCuts::creator);
 if(!s)return s;
 s=p.registerNode("wctSurfaceCurve",MTypeId(WCT_SURFACE_CURVE_NODE_ID),WctSurfaceCurve::creator,WctSurfaceCurve::initialize);
 if(!s)p.deregisterCommand("wctCurveEdgeCuts");return s;
}
WCT_EXPORT MStatus uninitializePlugin(MObject obj){
 MFnPlugin p(obj);MStatus s=p.deregisterNode(MTypeId(WCT_SURFACE_CURVE_NODE_ID));
 if(!s)return s;return p.deregisterCommand("wctCurveEdgeCuts");
}
