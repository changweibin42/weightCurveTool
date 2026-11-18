#pragma once
#include <maya/MPxCommand.h>
class WctProjectPoints : public MPxCommand {
public:
 static void* creator(){return new WctProjectPoints;}
 MStatus doIt(const MArgList&) override;
 bool isUndoable() const override{return false;}
};
