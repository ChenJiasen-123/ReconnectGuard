import argparse,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args();rows=json.load(open(a.input));fig,axes=plt.subplots(2,1,figsize=(10,5))
labels=['open_system_auth','assoc_req','deauth','sae_commit','sae_confirm','eapol_m1','eapol_m2','eapol_m3','eapol_m4']
for ax,r in zip(axes,rows):
 for i,label in enumerate(labels):
  xs=[t['ms_from_disconnect']/1000 for t in r['timeline'] if label in t['stages']]
  ax.scatter(xs,[i]*len(xs),s=22)
 ax.set_yticks(range(len(labels)),labels,fontsize=7);ax.set_title(r['variant']+': recovery='+r['recovery']+'; '+r['run_id'],fontsize=9);ax.set_xlabel('Seconds since disconnect reception')
fig.tight_layout();fig.savefig(a.output,dpi=180)
