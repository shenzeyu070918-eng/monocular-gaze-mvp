"""Guide scheduling and action evidence, independent of Qt/browser timers.

The preregistered thresholds/layout/scoring windows remain in acceptance.py.
Only the operational script has been extended after the first real run.
"""
PHASES=('scroll','tab','app','exclude','pause')
SYSTEM_DIALOGS=('usernotificationcenter','notificationcenter','securityagent','coreauthui','authorizationhost')


def foreground_block(front):
    if front is None:return '无法确认前台应用，请回测试页后重试'
    text=(front.get('app_name','')+' '+front.get('bundle_id','')).lower()
    if any(name in text for name in SYSTEM_DIALOGS):return '请先处理系统弹窗；处理完成并回到测试页后自动开始，不消耗注视时间'
    return None


def browser_front(front):
    return front is not None and any(name in front.get('bundle_id','').lower() for name in ('chrome','safari','firefox','edgemac','brave','vivaldi','arc','orion'))


def phase_at(seconds,action_s=4):
    if seconds<16:return dict(kind='target',id=int(seconds//2)+1)
    index=int((seconds-16)//action_s)
    return dict(kind='end') if index>=len(PHASES) else dict(kind='phase',phase=PHASES[index])


def phase_start(phase,action_s=4):return 16+PHASES.index(phase)*action_s


def instructions(phase,excluded_app='备忘录',pause_key='Control+Option+S'):
    return dict(scroll='向下滚动到第 9 块',tab='点击右侧彩色标签，停1.5秒，再点回来',
                app='按 ⌘Tab 切到 Finder 等普通应用',exclude=f'切到「{excluded_app}」',
                pause=f'回测试页，按 {pause_key} 暂停')[phase]


def temporary_exclusions(configured,app,bundle):
    """Copy, never mutate settings or config/local.yaml."""
    values=list(configured);added=[v for v in (app,bundle) if v not in values]
    return tuple(values+added),added


def native_action(phase,front,browser_pid,excluded):
    if front is None or foreground_block(front):return False
    denied=front.get('app_name') in excluded or front.get('bundle_id') in excluded
    if phase=='exclude':return denied
    return phase=='app' and front.get('pid')!=browser_pid and not denied and not front.get('own_ui',False)


class ActionSteps:
    """Untimed causal action steps. One continuous hold, then visible feedback.

    Browser callbacks supply scroll/visibilitychange evidence; native callbacks
    supply app identity, and the attention worker supplies pause/resume state.
    Never infer an action just from a prompt or elapsed wall-clock time.
    """
    def __init__(self,stable_s=1.5,feedback_s=1.):
        self.stable_ns=int(stable_s*1e9);self.feedback_ns=int(feedback_s*1e9)
        self.index=-1;self.entered_ns=None;self.hold_ns=None;self.hold_key=None
        self.finished=False;self.feedback=None;self.result_ns=None;self.paused_since=None
        self.first_pause_ns=None;self.resume_ready=False;self.events=[]

    @property
    def phase(self):return PHASES[self.index] if 0<=self.index<len(PHASES) else None

    def start(self,now):
        if self.index==-1:self._enter(0,now)

    def _enter(self,index,now):
        self.index=index;self.hold_ns=self.hold_key=None;self.feedback=None;self.result_ns=None
        self.paused_since=None;self.first_pause_ns=None;self.resume_ready=False;self.entered_ns=now
        if index==len(PHASES):self.finished=True;return
        self.events.append(dict(kind='phase',phase=self.phase,time_ns=now,step_id=index))

    def observe(self,phase,now,condition,key,source,**extra):
        if phase!=self.phase or self.feedback is not None:return
        if not condition:self.hold_ns=self.hold_key=None;return
        if self.hold_key!=key:self.hold_ns=now;self.hold_key=key
        if now-self.hold_ns>=self.stable_ns:
            self._complete(now,'completed',source,started_ns=self.hold_ns,**extra)

    def pause_state(self,now,paused,recording):
        if self.phase!='pause' or self.feedback is not None:return
        if not self.resume_ready:
            if paused:
                if self.paused_since is None:self.paused_since=now
                if now-self.paused_since>=self.stable_ns:self.resume_ready=True;self.first_pause_ns=self.paused_since
            else:self.paused_since=None
            return
        self.observe('pause',now,not paused and recording,'resumed','session_pause_resume',paused_ns=self.first_pause_ns)

    def _complete(self,now,result,source,**extra):
        self.feedback=result;self.result_ns=now;self.hold_ns=None
        if result=='completed':self.events.append(dict(kind='action',phase=self.phase,step_id=self.index,time_ns=now,source=source,**extra))
        self.events.append(dict(kind='step_result',phase=self.phase,step_id=self.index,time_ns=now,result=result))

    def skip(self,now,step_id):
        if self.phase is None or self.feedback is not None or step_id!=self.index:return False
        self._complete(now,'skipped','user_skip');return True

    def tick(self,now):
        if self.feedback is not None and now-self.result_ns>=self.feedback_ns:self._enter(self.index+1,now)

    def drain(self):
        events=self.events;self.events=[];return events

    def snapshot(self,now):
        held=0 if self.hold_ns is None else max(0,(now-self.hold_ns)/1e9)
        return dict(phase=self.phase,step_id=self.index,feedback=self.feedback,hold_s=held,
                    resume_ready=self.resume_ready,finished=self.finished)
