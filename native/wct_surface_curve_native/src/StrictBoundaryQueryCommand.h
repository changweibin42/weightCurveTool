#pragma once
#include <maya/MPxCommand.h>
class WctStrictBoundaryQueries : public MPxCommand {
public:
 static void* creator(){return new WctStrictBoundaryQueries;}
 MStatus doIt(const MArgList&) override;
 bool isUndoable() const override{return false;}
};
