import ast,os,json,hashlib,tempfile,contextlib,io
from pathlib import Path
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
class DriverTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.temp=tempfile.TemporaryDirectory();self.binary=Path(self.temp.name)/'wct.mll';self.binary.write_bytes(b'validated binary');self.pref={};self.command_calls=[]
  def plugin(*a,**kw):
   if kw.get('loaded'):return True
   if kw.get('version'):return '1.8.0'
   if kw.get('command'):return ['wctCurveEdgeCuts','wctStrictBoundaryQueries']
   if kw.get('path'):return str(self.binary)
  def option(**kw):
   if 'exists' in kw:return kw['exists'] in self.pref
   if 'q' in kw:return self.pref[kw['q']]
   if 'sv' in kw:self.pref[kw['sv'][0]]=kw['sv'][1]
   if 'remove' in kw:self.pref.pop(kw['remove'],None)
  def command(*args):
   self.command_calls.append(args);return [0,1,.5,1,0,0,-1,1,2]
  cmds=SimpleNamespace(pluginInfo=plugin,optionVar=option,wctStrictBoundaryQueries=command)
  p=ROOT/'weightCurveTool/tools/NativeStrictQueries.py';tree=ast.parse(p.read_text());tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
  self.ns=dict(os=os,json=json,hashlib=hashlib,OrderedDict=OrderedDict,np=np,cmds=cmds);exec(compile(tree,str(p),'exec'),self.ns)
  self.graph=[{1:1.},{0:1.}]
 def tearDown(self):self.temp.cleanup();self.env.stop()
 def calculate(self):
  with contextlib.redirect_stdout(io.StringIO()):return self.ns['compute_if_enabled']('mesh','curve',[0,1],1e-7,4,self.graph)
 def test_no_audit_keeps_python_path(self):self.assertIsNone(self.calculate());self.assertFalse(self.command_calls)
 def test_audit_mode_can_exercise_unvalidated_binary(self):
  os.environ.update(WCT_NATIVE_STRICT_AUDIT='1',WCT_NATIVE_STRICT_MODE='3');self.assertEqual(self.calculate(),{0:.5,1:None})
 def test_passing_record_survives_restart_identity_recheck(self):
  self.ns['remember_audit'](3);self.ns['_IDENTITY']=None;self.assertEqual(self.ns['validated_mode'](),3);self.assertEqual(self.calculate(),{0:.5,1:None})
 def test_replaced_binary_invalidates_record(self):
  self.ns['remember_audit'](3);self.binary.write_bytes(b'new binary longer');self.assertIsNone(self.ns['validated_mode']())
 def test_explicit_disable_overrides_validated_record(self):
  self.ns['remember_audit'](3);os.environ['WCT_NATIVE_STRICT_QUERIES']='0';self.assertIsNone(self.calculate())
 def test_clear_record_disables_path(self):
  self.ns['remember_audit'](3);self.ns['clear_audit']();self.assertIsNone(self.calculate())
 def test_malformed_or_incomplete_native_batch_falls_back(self):
  self.ns['remember_audit'](3)
  for rows in ([0,1,.5,-1,1,2],[0,1,float('nan'),1,1,.1,-1,1,2],[0,1,.5,0,0,0,-1,1,2],[0,1,.5,2,0,0,-1,1,2],[0,2,.5,1,0,0,-1,1,2]):
   self.ns['cmds'].wctStrictBoundaryQueries=lambda *a,r=rows:r;self.assertIsNone(self.calculate())
 def test_old_plugin_does_not_use_new_command(self):
  self.ns['remember_audit'](3);self.ns['cmds'].pluginInfo=lambda *a,**kw:'1.7.0' if kw.get('version') else True;self.assertIsNone(self.calculate())
 def test_missing_command_or_failed_command_falls_back(self):
  self.ns['remember_audit'](3)
  def fail(*a):raise RuntimeError('failed native command')
  self.ns['cmds'].wctStrictBoundaryQueries=fail;self.assertIsNone(self.calculate())
 def test_payload_has_exact_graph_and_requested_vertices(self):
  text=self.ns['_payload'](self.graph,[1,0]);self.assertEqual(text.split(),['1','2','1','2','0','1','1','0'])
 def test_invalid_record_mode_is_not_accepted(self):
  record=dict(binary=self.ns['binary_identity'](),mode=99);self.pref[self.ns['PREFERENCE']]=json.dumps(record);self.assertIsNone(self.ns['validated_mode']())
if __name__=='__main__':unittest.main()
