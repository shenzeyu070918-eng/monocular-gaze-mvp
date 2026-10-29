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
    initial_app=scenes[0].get('app_name') if scenes else None
    for step in steps:
        possible=[s for s in scenes if s['scene_id'] not in used and not s.get('dynamic') and 0<=s['start_ns']-step['time_ns']<=4500000000 and ((s.get('app_name')!=initial_app) if step['phase']=='app' else (s.get('app_name')==initial_app))]
        if possible:
            s=min(possible,key=lambda x:x['start_ns']);used.add(s['scene_id']);detected+=1
    valid_reference=list(screen_size)==[1512,982]
    return dict(preregistration=PREREGISTRATION,reference_geometry=valid_reference,expected_targets=8,recorded_targets=len(targets),
                hard_correct_s=correct,valid_fixation_s=denominator,hard_fraction=correct/denominator if denominator else None,
                hard_threshold=HARD_THRESHOLD,hard_pass=valid_reference and len(targets)==8 and denominator>0 and correct/denominator>=HARD_THRESHOLD,
                expected_transitions=3,recorded_phase_prompts=len(steps),detected_transitions=detected,
                transition_pass=len(steps)==3 and detected==3,transition_note='按提示后的有序场景匹配；必须另确认实际执行了滚动/切标签/切应用，未执行不能算检出。',
                cpu_baseline=baseline,cpu_recording=running,extra_process_tree_cpu=extra,cpu_pass=None if extra is None else extra<CPU_EXTRA_THRESHOLD,
                self_window_zero_residue_pass=None,self_window_note='需受控原生窗口排除测试，不能凭没有观察到窗口就自动判通过。',
                overall_pass=None,overall_note='人工执行确认、自窗口排除与性能证据齐全后才可验收；不自动宣称全部通过。')
