"""物理适配回归：拒绝接管在用网卡，部分启动失败后恢复原状态。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.platform != 'linux':
    raise unittest.SkipTest('physical Lab requires Linux')
from rg_platform.physical import PhysicalLab


class PhysicalTest(unittest.TestCase):
    def lab(self, tmp):
        lab = PhysicalLab.__new__(PhysicalLab)
        lab.out = Path(tmp)
        lab.meta, lab.originals = {}, []
        lab.ap_if, lab.sta_if = 'wlan1', 'wlan2'
        net, rf = lab.out/'net', lab.out/'rf'
        for n in (1, 2):
            iface = net/f'wlan{n}'
            (iface/'device/mt7921u').mkdir(parents=True)
            (iface/'device/driver').symlink_to('mt7921u')
            (iface/f'phy{n}').mkdir()
            (iface/'phy80211').symlink_to(f'phy{n}')
            r = rf/f'rfkill{n}'
            r.mkdir(parents=True)
            (r/'name').write_text(f'phy{n}')
            (r/'soft').write_text('1')
        return lab, net, rf

    def test_inventory_refuses_active_interface_before_mutation(self):
        for link in ({'flags':['UP'],'addr_info':[]}, {'flags':[], 'addr_info':[{'local':'1.2.3.4'}]}):
            with tempfile.TemporaryDirectory() as tmp:
                lab, net, rf = self.lab(tmp)
                with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.RFKILL_ROOT',rf), patch('rg_platform.physical.run',return_value=json.dumps([link])) as command:
                    with self.assertRaises(RuntimeError):
                        lab._inventory()
                self.assertEqual(command.call_args.args[:3], ('ip','-j','addr'))
                self.assertEqual(lab.originals, [])

    def test_inventory_records_identity_and_restores_only_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab, net, rf = self.lab(tmp)
            def reply(*args):
                if args[0]=='ip':
                    return json.dumps([{'flags':[], 'addr_info':[], 'address':'00:11:22:33:44:55'}])
                return 'yes'
            with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.RFKILL_ROOT',rf), patch('rg_platform.physical.run',side_effect=reply):
                lab._inventory()
            self.assertEqual(len(lab.originals), 2)
            lab.originals[0]['touched'] = True
            with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.run') as command:
                self.assertEqual(lab.restore_radios(), [])
            self.assertEqual([c.args for c in command.call_args_list], [('ip','link','set','wlan1','down'),('nmcli','dev','set','wlan1','managed','yes'),('rfkill','block','1')])

    def test_missing_phy_is_reported_and_other_interface_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab, net, rf = self.lab(tmp)
            with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.RFKILL_ROOT',rf), patch('rg_platform.physical.run',side_effect=lambda *a: json.dumps([{'flags':[],'addr_info':[], 'address':'x'}]) if a[0]=='ip' else 'no'):
                lab._inventory()
            for item in lab.originals:
                item['touched']=True
            lab.originals[0]['phy']='wrong'
            with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.run') as command:
                errors=lab.restore_radios()
            self.assertEqual(len(errors), 1)
            self.assertIn('wlan1', errors[0])
            self.assertIn(('rfkill','block','2'), [c.args for c in command.call_args_list])

    def test_restore_waits_for_asynchronous_namespace_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab, net, rf = self.lab(tmp)
            with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.RFKILL_ROOT',rf), patch('rg_platform.physical.run',side_effect=lambda *a: json.dumps([{'flags':[],'addr_info':[], 'address':'x'}]) if a[0]=='ip' else 'yes'):
                lab._inventory()
            lab.originals[0]['touched'] = True
            (net/'wlan1').rename(net/'returning')
            with patch('rg_platform.physical.NET_ROOT',net), patch('rg_platform.physical.run'), patch('rg_platform.physical.time.sleep',side_effect=lambda _: (net/'returning').rename(net/'wlan1')) as sleep:
                self.assertEqual(lab.restore_radios(), [])
                sleep.assert_called_once()
