#pragma once
#include <maya/MPxCommand.h>
class WctCurveEdgeCuts : public MPxCommand {
public:
 static void* creator(){return new WctCurveEdgeCuts;}
 MStatus doIt(const MArgList&) override;
 bool isUndoable() const override{return false;}
};
