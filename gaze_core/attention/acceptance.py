"""Frozen before real M9 measurements; see docs/m9-acceptance.md (9f9b655)."""
import os,subprocess,time

HARD_THRESHOLD=.60
CPU_EXTRA_THRESHOLD=10.
PREREGISTRATION='9f9b655:docs/m9-acceptance.md'


def layout(screen):
    # Same normalized layout on other screens, but reference Mac pass/fail is
    # only applicable at the preregistered full logical size.
    return [dict(id=str(n+1),rect=[(48+732*(n%2))/1511,(65+216*(n//2))/981,(732+732*(n%2))/1511,(259+216*(n//2))/981]) for n in range(8)]


def parse_cpu_time(text):
    text=text.strip();days=0
    if '-' in text:
        d,text=text.split('-',1);days=int(d)
    result=0.
    for part in text.split(':'):result=result*60+float(part)
    return days*86400+result


def cpu_snapshot():
    """Own process tree CPU seconds + WindowServer. No titles or app content."""
    import resource
    rows=subprocess.check_output(['ps','-axo','pid=,ppid=,time='],text=True,timeout=3).splitlines()
    processes={}
    for line in rows:
        fields=line.split()
        if len(fields)==3:
            try:processes[int(fields[0])]=(int(fields[1]),parse_cpu_time(fields[2]))
            except ValueError:pass
    children=set();frontier={os.getpid()}
    while frontier:
        found={pid for pid,(parent,_) in processes.items() if parent in frontier}-children
        children|=found;frontier=found
    own=resource.getrusage(resource.RUSAGE_SELF);retired=resource.getrusage(resource.RUSAGE_CHILDREN)
    total=own.ru_utime+own.ru_stime+retired.ru_utime+retired.ru_stime+sum(processes[pid][1] for pid in children)
    server=None
    try:
        ids=subprocess.check_output(['pgrep','-x','WindowServer'],text=True,timeout=3).split()
        server=sum(processes[int(pid)][1] for pid in ids if int(pid) in processes)
    except (subprocess.SubprocessError,ValueError):pass
    return dict(time_ns=time.perf_counter_ns(),tree_cpu_s=total,windowserver_cpu_s=server)


def cpu_difference(before,after):
    elapsed=(after['time_ns']-before['time_ns'])/1e9
    def percent(key):
        a,b=before.get(key),after.get(key)
        return None if elapsed<=0 or a is None or b is None or b<a else (b-a)/elapsed*100
    return dict(seconds=elapsed,process_tree_percent_one_core=percent('tree_cpu_s'),windowserver_percent_one_core=percent('windowserver_cpu_s'))


def score(fixations,events,screen_size):
    targets=[e for e in events if e.get('type')=='acceptance' and e.get('kind')=='target']
    denominator=correct=0.
    for target in targets:
        a=target['time_ns']+500000000;b=target['time_ns']+2000000000;x0,y0,x1,y1=target['rect']
        for item in fixations:
            if item.get('excluded_reason') or item.get('scene_id') is None:continue
            duration=max(0,min(b,item['end_ns'])-max(a,item['start_ns']))/1e9
            denominator+=duration;x,y=item['center']
            if x0<=x<=x1 and y0<=y<=y1:correct+=duration
    baseline=next((e.get('measurement') for e in events if e.get('kind')=='cpu_baseline'),None)
    running=next((e.get('measurement') for e in events if e.get('kind')=='cpu_recording'),None)
    extra=None
    if baseline and running and baseline.get('process_tree_percent_one_core') is not None and running.get('process_tree_percent_one_core') is not None:
        extra=running['process_tree_percent_one_core']-baseline['process_tree_percent_one_core']
    steps=[e for e in events if e.get('kind')=='phase' and e.get('phase') in ('scroll','tab','app')]
    scenes=[e for e in events if e.get('type')=='scene'];used=set();detected=0
    protocol=next((e for e in events if e.get('kind')=='guide_protocol'),None)
    # Preparation may have recorded a permission/notification scene. The task
    # browser, not that first preparation frame, is the transition reference.
    initial_app=(protocol or {}).get('browser_app') or (scenes[0].get('app_name') if scenes else None)
    action_evidence={}
    for phase in ('scroll','tab','app','exclude','pause'):
        prompt=next((e for e in events if e.get('kind')=='phase' and e.get('phase')==phase),None)
        actions=[e for e in events if e.get('kind')=='action' and e.get('phase')==phase]
        if prompt is None:action_evidence[phase]=dict(executed=False,reason='未进入该步骤');continue
        start=prompt['time_ns']
        self_paced=(protocol or {}).get('version',0)>=3
        following=[e['time_ns'] for e in events if e.get('kind')=='phase' and e['time_ns']>start]
        end=min(following) if self_paced and following else float('inf') if self_paced else start+int((protocol or {}).get('action_s',4)*1e9)
        skipped=any(e.get('kind')=='step_result' and e.get('phase')==phase and e.get('result')=='skipped' and start<=e['time_ns']<=end for e in events)
        observed=[a for a in actions if start-100000000<=a['time_ns']<=end]
        if phase=='pause':
            paused=next((e for e in events if e.get('type')=='state' and e.get('status')=='user_paused' and start<=e['time_ns']<=end),None)
            resumed=paused is not None and any(e.get('type')=='state' and e.get('status')=='recording' and paused['time_ns']<e['time_ns']<=end+500000000 for e in events)
            ok=bool(resumed)
        elif phase=='exclude':
            ok=bool(observed) and any(e.get('type')=='state' and e.get('status')=='excluded_app' and start<=e['time_ns']<=end+500000000 for e in events)
        else:ok=bool(observed)
        if skipped:ok=False
        action_evidence[phase]=dict(executed=ok,skipped=skipped,reason='用户跳过' if skipped else '已观测操作' if ok else ('旧引导无独立操作证据' if protocol is None else '未观测到操作或未停留足够时间'))
    transition_details={}
    if (protocol or {}).get('version',0)>=3:
        for phase in ('scroll','tab','app'):
            executed=action_evidence[phase]['executed']
            action=next((e for e in events if e.get('kind')=='action' and e.get('phase')==phase),None) if executed else None
            found=None
            if action is not None:
                completed=action['time_ns'];began=action.get('observed_ns',action.get('started_ns',completed-1500000000))
                expected=action.get('app_name') or initial_app
                possible=[row for row in scenes if row['scene_id'] not in used and not row.get('dynamic')
                          and began-500000000<=row['start_ns']<=completed+4500000000
                          and row.get('app_name')==expected]
                if possible:found=min(possible,key=lambda row:abs(row['start_ns']-began));used.add(found['scene_id']);detected+=1
            transition_details[phase]=dict(executed=executed,detected=found is not None,
                completed_ns=None if action is None else action['time_ns'],scene_id=None if found is None else found['scene_id'],
                reason='动作未执行' if not executed else '已执行且检出' if found is not None else '动作已执行但切换未检出')
    else:
        for step in steps:
            possible=[s for s in scenes if s['scene_id'] not in used and not s.get('dynamic') and 0<=s['start_ns']-step['time_ns']<=4500000000 and ((s.get('app_name')!=initial_app) if step['phase']=='app' else (s.get('app_name')==initial_app))]
            if possible:
                s=min(possible,key=lambda x:x['start_ns']);used.add(s['scene_id']);detected+=1
    missing_transitions=[name for name,value in transition_details.items() if value['executed'] and not value['detected']]
    missing=[name for name,value in action_evidence.items() if not value['executed']]
    zero=next((e for e in events if e.get('kind')=='self_window_test'),None)
    zero_pass=None if zero is None else zero.get('passed')
    valid_reference=list(screen_size)==[1512,982]
    transition_pass=len(steps)==3 and detected==3
    if protocol is not None:transition_pass=transition_pass and all(action_evidence[p]['executed'] for p in ('scroll','tab','app'))
    return dict(preregistration=PREREGISTRATION,reference_geometry=valid_reference,expected_targets=8,recorded_targets=len(targets),
                hard_correct_s=correct,valid_fixation_s=denominator,hard_fraction=correct/denominator if denominator else None,
                hard_threshold=HARD_THRESHOLD,hard_pass=valid_reference and len(targets)==8 and denominator>0 and correct/denominator>=HARD_THRESHOLD,
                expected_transitions=3,recorded_phase_prompts=len(steps),detected_transitions=detected,
                transition_pass=transition_pass,transition_note='有序场景匹配与独立动作证据分别报告；未执行不算检出。',
                action_evidence=action_evidence,steps_not_executed=missing,transition_details=transition_details,executed_but_not_detected=missing_transitions,
                cpu_baseline=baseline,cpu_recording=running,extra_process_tree_cpu=extra,cpu_pass=None if extra is None or (protocol or {}).get('version',0)>=3 and not (running or {}).get('comparison_valid',False) else extra<CPU_EXTRA_THRESHOLD,
                self_window_zero_residue_pass=zero_pass,self_window_evidence=zero,
                self_window_note='需运行受控原生窗口排除工具；没有证据时保持未验证。',
                overall_pass=None,overall_note='四项标准与步骤完成情况分别列出；人工确认真实操作后才可整体验收。')


def result_text(result):
    """UI-only formatting; never recalculate or relax a criterion."""
    def verdict(value):return '未验证' if value is None else '通过' if value else '未通过'
    def number(value,suffix=''):return '无数据' if value is None else f'{value:.1f}{suffix}'
    fraction=result.get('hard_fraction')
    lines=['M9 引导验收（门槛沿用预注册）',
           f"硬归属 ≥60%：{verdict(result.get('hard_pass'))} · {number(None if fraction is None else fraction*100,'%')}",
           f"场景切换 3/3：{verdict(result.get('transition_pass'))} · {result.get('detected_transitions',0)}/3",
           f"自窗口零残留：{verdict(result.get('self_window_zero_residue_pass'))}",
           f"额外 CPU <单核10%：{verdict(result.get('cpu_pass'))} · {number(result.get('extra_process_tree_cpu'),'%')}"]
    names=dict(scroll='滚动',tab='切到右侧测试标签',app='切普通应用',exclude='进入排除应用',pause='暂停并恢复')
    missing=result.get('steps_not_executed',[])
    lines.append('未执行/未观测步骤：'+('、'.join(names.get(p,p) for p in missing) if missing else '无（独立证据齐全）'))
    lines.append('已执行但切换未检出：'+('、'.join(names.get(p,p) for p in result.get('executed_but_not_detected',[])) or '无'))
    lines.append('没有执行的操作不自动视为通过；自窗口工具结果单独留证。')
    return '\n'.join(lines)
