"""将实测阶段时间转为可导出的图；不将中位数之和解释为总中位数。"""
import argparse,json,statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
rows=json.loads(Path(a.input).read_text());cases=['client_disconnect_reconnect','ap_deauth','ap_disassoc'];fields=['wait_to_auth_ms','auth_to_m4_ms','m4_to_connected_ms','connected_to_probe_finished_ms'];labels=['Wait to auth/assoc','Auth/assoc to M4','M4 to connected event','Connected to probe finish']
fig,ax=plt.subplots(figsize=(10,4));left=[0.]*3
for field,label in zip(fields,labels):
 values=[statistics.median([r[field] for r in rows if r['case']==case and r[field] is not None]) for case in cases]
 ax.barh(cases,values,left=left,label=label);left=[x+y for x,y in zip(left,values)]
ax.set_xlabel('Stage median (ms); sums are not medians of total duration');ax.legend(loc='lower right',fontsize=8);fig.tight_layout();fig.savefig(a.output,dpi=180)
