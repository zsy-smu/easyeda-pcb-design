"""Run transport/guard tests against a fake WebSocket, never a user's EDA."""
from pathlib import Path
import shutil
import subprocess
import unittest

class BridgeClientTests(unittest.TestCase):
    def test_node_protocol_guards_and_file_receipts(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node runtime unavailable; no bridge client tests executed')
        script=Path(__file__).with_suffix('.cjs')
        r=subprocess.run([node,'--test','--test-reporter=tap',str(script)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        self.assertEqual(0,r.returncode,r.stdout+'\n'+r.stderr)
        self.assertIn('# fail 0',r.stdout)
        self.assertIn('# tests ',r.stdout)

if __name__=='__main__':unittest.main()
