"""RSS字节口径、读数失败与本机采样验证。"""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.platform import memory

class MemoryTests(unittest.TestCase):
    def test_proc_uses_rss_not_virtual_or_peak(self):
        text='VmSize: 900000 kB\nVmHWM: 80000 kB\nVmRSS: 4096 kB\n'
        with patch('builtins.open',return_value=io.StringIO(text)):
            self.assertEqual(memory._proc_rss(),4194304)
    def test_proc_missing_or_invalid_is_not_zero(self):
        for text in ('VmSize: 900 kB\n','VmRSS: -1 kB\n','VmRSS: 12 MB\n'):
            with patch('builtins.open',return_value=io.StringIO(text)):
                with self.assertRaises(ValueError):memory._proc_rss()
    def test_failure_has_source_and_reason(self):
        with patch.object(memory,'_windows_rss',side_effect=OSError('blocked')),patch.object(memory,'_proc_rss',side_effect=OSError('blocked')):
            sample=memory.process_memory_snapshot()
        self.assertIsNone(sample['memory_rss_bytes'])
        self.assertEqual(sample['memory_error'],'blocked')
        self.assertTrue(sample['memory_source'])
    def test_native_process_read(self):
        sample=memory.process_memory_snapshot()
        self.assertIsNone(sample['memory_error'],sample)
        self.assertGreater(sample['memory_rss_bytes'],0)

if __name__=='__main__':unittest.main()
