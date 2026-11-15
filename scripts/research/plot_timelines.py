import argparse,json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args();rows=json.loads(Path(a.input).read_text());fig,axes=plt.subplots(len(rows),1,figsize=(10,6))
for ax,row in zip(axes,rows):
 fs=row['frames'];ax.scatter([r['ms'] for r in fs],range(len(fs)))
 for i,r in enumerate(fs):ax.annotate(' / '.join(r['stage'])+' [frame '+str(r['frame'])+']',(r['ms'],i),xytext=(8,3),textcoords='offset points',fontsize=8)
 ax.set_title(row['policy']+': '+row['auth_path']+'; '+row['run_id'],fontsize=10);ax.set_xlabel('ms from first referenced authentication frame');ax.set_yticks([]);ax.margins(x=.35,y=.2)
fig.tight_layout();fig.savefig(a.output,dpi=180)
