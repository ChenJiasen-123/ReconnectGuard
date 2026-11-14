"""显式指定的两块闲置 USB 网卡；使用系统 hostapd / wpa_supplicant。

与 Framework hwsim 平台是不同实验条件。所有无线流量只针对本次专用 SSID。
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from .lab import Lab, ROOT, run
from .control import Control
from .evidence import archive_configs


NET_ROOT = Path('/sys/class/net')
RFKILL_ROOT = Path('/sys/class/rfkill')


class PhysicalLab(Lab):
    framework_ap = False

    def __init__(self, mode='stock', *, output_root=None, ap_iface=None, sta_iface=None):
        if mode != 'stock':
            raise ValueError('实体入口目前只支持系统原版客户端 stock')
        if not ap_iface or not sta_iface or ap_iface == sta_iface:
            raise ValueError('必须明确指定两块不同的实验网卡')
        super().__init__(mode, output_root=output_root)
        self.ap_if, self.sta_if = ap_iface, sta_iface
        self.originals = []
        self.meta.update(radio='physical', ap_implementation='distribution_hostapd',
                         scope='physical WPA3 HE experiment, not Framework hwsim')

    def _inventory(self):
        # 在任何修改前检查两块网卡；拒绝有地址、在用、非预期驱动的接口。
        for iface in (self.ap_if, self.sta_if):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', iface):
                raise ValueError('非法接口名')
            device = NET_ROOT / iface
            driver = (device / 'device/driver').resolve().name
            if driver != 'mt7921u':
                raise RuntimeError(f'{iface}: 预期 mt7921u，实际 {driver}')
            link = __import__('json').loads(run('ip', '-j', 'addr', 'show', 'dev', iface))[0]
            if link.get('addr_info') or 'UP' in link['flags']:
                raise RuntimeError(f'{iface}: 接口在用，拒绝接管')
            phy = (device / 'phy80211').resolve().name
            rf = next(p for p in RFKILL_ROOT.glob('rfkill*')
                      if (p / 'name').read_text().strip() == phy)
            managed = run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'dev', 'show', iface)
            if managed not in ('yes', 'no'):
                raise RuntimeError('无法确定 NetworkManager 接口管理状态')
            self.originals.append(dict(iface=iface, phy=phy, mac=link['address'],
                                       rfkill=rf.name[6:], soft=(rf/'soft').read_text().strip(),
                                       managed=managed, touched=False))
        self.meta['original_interfaces'] = self.originals
        self.save('physical-originals.json', self.originals)

    def start(self):
        self.lock = open('/run/reconnectguard/platform.lock', 'w')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._inventory()
        for tool in ('hostapd', 'wpa_supplicant', 'tcpdump', 'iw', 'ip', 'ping'):
            if not shutil.which(tool):
                raise RuntimeError('缺少依赖: ' + tool)
        for item, role in zip(self.originals, ('ap', 'sta')):
            item['touched'] = True
            run('nmcli', 'dev', 'set', item['iface'], 'managed', 'no')
            run('rfkill', 'unblock', item['rfkill'])
            ns = 'rg-' + role + '-' + self.run_id[-6:]
            run('ip', 'netns', 'add', ns)
            self.namespaces.append(ns)
            run('iw', 'phy', item['phy'], 'set', 'netns', 'name', ns)
            run('ip', '-n', ns, 'link', 'set', 'lo', 'up')
        self.phys = [x['phy'] for x in self.originals]
        ctrl_ap, ctrl_sta = self.runtime/'ap-ctrl', self.runtime/'sta-ctrl'
        ctrl_ap.mkdir(); ctrl_sta.mkdir()
        self.ap_ctrl, self.sta_ctrl = ctrl_ap/self.ap_if, ctrl_sta/self.sta_if
        self.ap_config, self.sta_config = self.runtime/'ap.conf', self.runtime/'sta.conf'
        # 先使用 2.4 GHz / 20 MHz HE，减少射频变量；不改变全局监管域。
        ap = (ROOT/'configs/ap.conf').read_text()
        ap = ap.replace('ctrl_interface=/var/run/hostapd', 'ctrl_interface='+str(ctrl_ap))
        ap = ap.replace('ssid=ReconnectGuardLab', 'ssid=RG-Physical-'+self.run_id[-6:])
        ap += '\ninterface='+self.ap_if+'\ncountry_code='+run('iw','reg','get').split('country ',1)[1][:2]+'\nwmm_enabled=1\nieee80211ax=1\n'
        sta = (ROOT/'configs/sta.conf').read_text()
        sta = sta.replace('ctrl_interface=/var/run/wpa_supplicant', 'ctrl_interface='+str(ctrl_sta))
        sta = sta.replace('ssid="ReconnectGuardLab"', 'ssid="RG-Physical-'+self.run_id[-6:]+'"')
        self.ap_config.write_text(ap); self.sta_config.write_text(sta)
        os.chmod(self.sta_config, 0o600)
        self.meta['runtime_configs'] = archive_configs(self.out, {'ap':self.ap_config, 'sta':self.sta_config})
        monitor = 'rgmon'+self.run_id[-6:]
        run('ip','netns','exec',self.namespaces[0],'iw','phy',self.phys[0],'interface','add',monitor,'type','monitor')
        run('ip','-n',self.namespaces[0],'link','set',monitor,'up')
        self.monitor = monitor
        ap_bin, sta_bin = Path(shutil.which('hostapd')), Path(shutil.which('wpa_supplicant'))
        self.spawn(self.namespaces[0], [ap_bin, '-dd', self.ap_config], 'ap')
        self.spawn(self.namespaces[1], [sta_bin, '-Dnl80211','-i',self.sta_if,'-c',self.sta_config,'-dd'], 'sta')
        self.wait_controls()
        ap_mac, sta_mac = (x['mac'] for x in self.originals)
        capture_filter = f'wlan addr1 {sta_mac} or wlan addr2 {sta_mac} or wlan addr3 {ap_mac}'
        capture = ['tcpdump','--immediate-mode','-B','4096','-i',monitor,'-s','0','-U',
                   '-w',self.out/'trace.pcap',capture_filter]
        self.spawn(self.namespaces[0], capture, 'capture')
        time.sleep(.3)
        self.check_health()
        for role, endpoint in (('ap', self.ap_ctrl), ('sta', self.sta_ctrl)):
            ctrl = Control(endpoint); ctrl.attach()
            t = threading.Thread(target=self.listen, args=(role,ctrl), daemon=True)
            t.start(); self.threads.append(t)
        for ns, iface, address in zip(self.namespaces,(self.ap_if,self.sta_if),('10.203.0.1/24','10.203.0.2/24')):
            run('ip','-n',ns,'addr','add',address,'dev',iface)
        sources = subprocess.check_output(['git','-C',str(ROOT),'ls-files'], text=True).splitlines()
        sources.append('rg_platform/physical.py')
        manifest = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                    for name in sorted(set(sources)) if (ROOT/name).is_file()}
        self.save('source-files.json', manifest)
        self.meta.update(kernel=run('uname','-r'), python_version=sys.version.split()[0],
                         source_manifest='source-files.json',
                         source_commit=run('git','-C',ROOT,'rev-parse','HEAD'),
                         source_diff_sha256=hashlib.sha256(subprocess.check_output(['git','-C',str(ROOT),'diff','HEAD'])).hexdigest(),
                         system_packages=run('dpkg-query','-W','hostapd','wpasupplicant','firmware-misc-nonfree'),
                         ap_iface=self.ap_if, sta_iface=self.sta_if, monitor_iface=monitor,
                         namespaces=self.namespaces, phys=self.phys,
                         capture={'argv':list(map(str,capture)), 'version':run('tcpdump','--version')},
                         binary_sha256={'ap':hashlib.sha256(ap_bin.read_bytes()).hexdigest(),'sta':hashlib.sha256(sta_bin.read_bytes()).hexdigest()},
                         processes=[{'role':r,'pid':p.pid,'process_epoch':epoch} for r,p,epoch in self.processes],
                         profile={'key_mgmt':'SAE','pairwise':'CCMP','pmf':'required','phy_requested':'802.11ax'},
                         initial_policy='cold_start', cache_clear='not_requested')
        self.save('meta.json',self.meta)
        self.command('sta','ENABLE_NETWORK 0','initial_connect')

    def spawn(self, ns, cmd, role):
        log = open(self.out/(role+'.log'),'w')
        env = os.environ.copy(); env.pop('PYTHONPATH',None)
        proc = subprocess.Popen(['ip','netns','exec',ns]+list(map(str,cmd)), cwd=ROOT,
                                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        from .lab import utc
        self.processes.append((role,proc,utc()))

    def restore_radios(self):
        errors = []
        # 删除 netns 名称后内核异步销毁网络空间；等待实体 PHY 返回。
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if all((NET_ROOT/x['iface']).exists() for x in self.originals if x['touched']):
                break
            time.sleep(.1)
        if hasattr(self, 'monitor'):
            # 命名空间删除后 PHY 及其接口已回到主命名空间。
            try:
                run('iw','dev',self.monitor,'del')
            except Exception as exc:
                errors.append('monitor restore: '+str(exc))
        for item in self.originals:
            if not item['touched']:
                continue
            try:
                device = NET_ROOT/item['iface']
                if not device.exists() or (device/'phy80211').resolve().name != item['phy']:
                    raise RuntimeError('实体接口未返回: '+item['iface'])
                run('ip','link','set',item['iface'],'down')
                run('nmcli','dev','set',item['iface'],'managed',item['managed'])
                run('rfkill','block' if item['soft']=='1' else 'unblock',item['rfkill'])
            except Exception as exc:
                errors.append('physical restore: '+str(exc))
        return errors
