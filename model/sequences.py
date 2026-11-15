"""明确的有限动作字母表；前置条件是已连接且双方缓存存在。"""
from dataclasses import dataclass,asdict
from collections import deque

ACTIONS=('disconnect','flush_ap','flush_sta','arm_reject','allow','reconnect')


@dataclass(frozen=True)
class Node:
    connected:bool=True
    ap_cache:bool=True
    sta_cache:bool=True
    rejecting:bool=False


def step(node, action):
    if action not in ACTIONS:raise ValueError('unknown action')
    v=asdict(node)
    if action=='disconnect' and node.connected:v['connected']=False
    elif not node.connected:
        if action=='flush_ap' and node.ap_cache:v['ap_cache']=False
        elif action=='flush_sta' and node.sta_cache:v['sta_cache']=False
        elif action=='arm_reject' and not node.rejecting:v['rejecting']=True
        elif action=='allow' and node.rejecting:v['rejecting']=False
        elif action=='reconnect':v.update(connected=True,ap_cache=True,sta_cache=True)
        else:raise ValueError('precondition: '+action)
    else:raise ValueError('precondition: '+action)
    return Node(**v)


def validate(plan,max_depth=6):
    if not plan or len(plan)>max_depth or plan[-1]!='reconnect':raise ValueError('plan must end in reconnect within depth')
    node=Node();edges=[]
    for action in plan:
        nxt=step(node,action);edges.append((repr(node),action,repr(nxt)));node=nxt
    return edges


def enumerate_plans(depth):
    queue=deque([(Node(),[])])
    while queue:
        node,plan=queue.popleft()
        if plan and plan[-1]=='reconnect':yield plan
        if len(plan)==depth:continue
        for action in ACTIONS:
            try:nxt=step(node,action)
            except ValueError:continue
            queue.append((nxt,plan+[action]))


def modeled_failure(plan):
    node=Node()
    for action in plan:
        if action=='reconnect' and node.rejecting and node.sta_cache:return True
        node=step(node,action)
    return False


def shortest_certificate(depth=6):
    shorter=[]
    for plan in enumerate_plans(depth):
        if modeled_failure(plan):
            return {'alphabet':list(ACTIONS),'initial':asdict(Node()),'metric':'number of harness operations; baseline and observation wait excluded',
                    'failure_predicate':'cached association under armed Deauth rejection policy; AP cache loss is not required',
                    'policy_semantics':'arm_reject installs repeated reason-9 rejection until full SAE or allow; not one frame',
                    'shortest':plan,'length':len(plan),'strictly_shorter_terminal_plans':[p for p in shorter if len(p)<len(plan)],
                    'scope':'deterministic cache-rejection abstraction; not all Wi-Fi actions or nondeterministic device executions'}
        shorter.append(plan)
    raise ValueError('no modeled counterexample within bound')
