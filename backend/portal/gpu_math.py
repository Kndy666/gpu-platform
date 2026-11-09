"""GPU resource arithmetic shared by the portal and the trusted node worker."""
from itertools import permutations

COMPUTE = 'gpu.platform/compute'
MEMORY = 'gpu.platform/memory-mib'
GATE = 'gpu.platform/allocation'

def sm_count(total, percent):
    return int(total) if percent == 100 else int(total) * int(percent) // 200 * 2

def validate_gpu_requests(requests, count, allow_max=False):
    if not isinstance(requests,list) or len(requests)!=count:
        raise ValueError('请为每张 GPU 设置算力和显存。')
    for index,request in enumerate(requests,1):
        if not isinstance(request,dict) or set(request)!={'compute_percent','memory_gib'}:
            raise ValueError(f'第 {index} 张 GPU 的配置格式不正确。')
        if not (allow_max and request['compute_percent']=='max') and (type(request['compute_percent']) is not int or not 2<=request['compute_percent']<=100):
            raise ValueError(f'第 {index} 张 GPU 的算力需为 2–100%。')
        if not (allow_max and request['memory_gib']=='max') and (type(request['memory_gib']) is not int or request['memory_gib']<1):
            raise ValueError(f'第 {index} 张 GPU 的显存至少为 1 GiB。')

def has_gpu_max(requests):
    return isinstance(requests,list) and any(isinstance(r,dict) and 'max' in r.values() for r in requests)

def _share_maximum(values,caps,total,minimum):
    if any(v!='max' and (v<minimum or v>cap) for v,cap in zip(values,caps)):return None
    remaining=(sum(caps) if total is None else total)-sum(v for v in values if v!='max')
    selected=[i for i,v in enumerate(values) if v=='max']
    if remaining<len(selected)*minimum or any(caps[i]<minimum for i in selected):return None
    result=[0 if v=='max' else v for v in values]
    pending=selected[:]
    while remaining>0 and pending:
        share=max(1,remaining//len(pending))
        for i in pending:
            amount=min(share,caps[i]-result[i],remaining)
            result[i]+=amount;remaining-=amount
        pending=[i for i in pending if result[i]<caps[i]]
    return result if all(v>=minimum for v in result) else None

def resolve_gpu_max(requests,limits):
    """Turn max selections into concrete integer quotas before reserving resources."""
    validate_gpu_requests(requests,len(requests),allow_max=True)
    if not has_gpu_max(requests):return [dict(r) for r in requests]
    devices=limits['devices']
    if len(devices)<len(requests):raise ValueError('GPU 设备信息不足，请稍后重试。')
    best=None;best_score=None
    for indices in permutations(range(len(devices)),len(requests)):
        group=[devices[i] for i in indices]
        compute=_share_maximum([r['compute_percent'] for r in requests],
            [limits['compute_per_card_percent']]*len(requests),limits['compute_total_percent'],2)
        memory=_share_maximum([r['memory_gib'] for r in requests],
            [min(d['memory_gib'],limits['memory_per_card_gib']) for d in group],limits['memory_total_gib'],1)
        if compute is None or memory is None:continue
        if not all(sm_count(d['sm_count'],p)>0 for d,p in zip(group,compute)):continue
        score=(-sum(compute),-sum(memory),indices)
        if best_score is None or score<best_score:
            best_score=score;best=[{'compute_percent':p,'memory_gib':m} for p,m in zip(compute,memory)]
    if best is None:raise ValueError('当前配置无法同时满足各卡额度，请调整其他行或 GPU 数量。')
    return best

def choose_devices(devices, leases, requests):
    """Reserve distinct physical GPUs, with both quotas fitting on every GPU."""
    usage = {d['uuid']: [0, 0, 0] for d in devices}
    for lease in leases:
        for a in lease:
            if a['uuid'] in usage:
                value = usage[a['uuid']]
                value[0] += a['compute_percent']; value[1] += a['memory_mib']; value[2] += a['sm_count']
    best=None;best_score=None
    for group in permutations([d for d in devices if d.get('ready',True)],len(requests)):
        allocations=[];remaining_compute=0;remaining_memory=0
        for slot,(device,request) in enumerate(zip(group,requests)):
            percent=request['compute_percent'];memory_mib=request['memory_gib']*1024
            used=usage[device['uuid']];sms=sm_count(device['sm_count'],percent)
            if not sms or used[0]+percent>100 or used[1]+memory_mib>device['usable_memory_mib'] or used[2]+sms>device['sm_count']:
                break
            remaining_compute+=100-used[0]-percent
            remaining_memory+=device['usable_memory_mib']-used[1]-memory_mib
            allocations.append({'slot':slot,'uuid':device['uuid'],'index':device['index'],'sm_count':sms,
                'compute_percent':percent,'actual_compute_percent':round(sms/device['sm_count']*100,2),'memory_mib':memory_mib})
        else:
            score=(remaining_compute,remaining_memory,tuple(d['index'] for d in group))
            if best_score is None or score<best_score:best=allocations;best_score=score
    return best
