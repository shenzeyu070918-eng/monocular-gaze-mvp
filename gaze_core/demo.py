"""Camera/UI-independent Demo layout, practice, feedback, scoring and undo."""
from math import ceil
import random
import uuid
from numbers import Integral
from .dwell import Button,DwellSelector
from .devices import finite


def button_layout(width,height,radii,options):
    """Preserve physical accuracy scale; DPR has already been handled by Qt.

    Each axis uses the same ring radius as overlay.mapping.ScreenRect.radii.
    The page control, when needed, occupies one equally large cell.
    """
    rx,ry=radii
    minimum_w=max(options.min_button_px,3*rx)
    minimum_h=max(options.min_button_px,3*ry)
    gap_x=max(rx,options.progress_px*2);gap_y=max(ry,options.progress_px*2)
    area_w=width-2*options.padding_px
    area_h=height-options.header_px-options.footer_px-2*options.padding_px
    candidates=[]
    for columns in range(1,4):
        for rows in range(1,3):
            w=(area_w-(columns-1)*gap_x)/columns
            h=(area_h-(rows-1)*gap_y)/rows
            if w>=minimum_w and h>=minimum_h:candidates.append((columns*rows,columns,rows,w,h))
    if not candidates:raise ValueError('屏幕放不下满足当前误差半径的按钮，请调整屏幕设置或重新校准。')
    count,columns,rows,w,h=max(candidates)
    if count<2:raise ValueError('当前误差半径只容得下一个按钮，无法安全分页，请重新校准。')
    return tuple((options.padding_px+c*(w+gap_x),options.header_px+options.padding_px+r*(h+gap_y),w,h)
                 for r in range(rows) for c in range(columns))


class DemoRun:
    def __init__(self, app, options, width,height,radii, *, group, stale_ms=300., rng=None):
        if app not in {'board','game'}:raise ValueError('未知 Demo 应用')
        if group not in {'B','C'}:raise ValueError('请先完成九点或点击粗校准')
        self.app,self.options,self.width,self.height,self.group=app,options,width,height,group
        self.radii=radii;self.cells=button_layout(width,height,radii,options)
        self.selector=DwellSelector(options,radii,stale_ms=stale_ms)
        self.rng=rng or random.Random();self.run_id=uuid.uuid4().hex
        self.phase='practice' if options.practice else 'playing'
        self.page=0;self.target_id=None;self.presented_ns=None;self.started_ns=None
        self.feedback_until=0;self.flash_until=0;self.feedback_text='';self.speech_pending=False
        self.speech_failed=False;self.reason=None;self.events=[];self.pending=None
        self.selections=[];self.score=0;self.reactions=[];self.last_sequence=-1
        self._reported=False
        self._configure()

    @property
    def active(self):return self.phase!='closed'

    @property
    def pages(self):return ceil(6/(len(self.cells)-1)) if len(self.cells)<6 else 1

    def _configure(self):
        self.presented_ns=None
        if self.phase=='practice':
            w,h=self.cells[0][2:]
            buttons=[Button('practice',((self.width-w)/2,(self.height-h)/2,w,h))]
        elif self.phase=='playing':
            n=6 if self.pages==1 else len(self.cells)-1
            ids=list(range(self.page*n,min(6,(self.page+1)*n)))
            buttons=[Button(str(i),self.cells[j]) for j,i in enumerate(ids)]
            if self.pages>1:buttons.append(Button('next',self.cells[-1]))
            if self.app=='game':
                choices=[str(i) for i in ids if str(i)!=self.target_id]
                self.target_id=self.rng.choice(choices or [str(ids[0])])
        else:buttons=[]
        self.buttons=tuple(buttons)
        self.selector.set_buttons([b for b in buttons if self.phase!='playing' or self.app!='game' or b.id in {self.target_id,'next'}])

    def presented(self, now_ns):
        if self.presented_ns is not None:return
        self.presented_ns=now_ns
        if self.phase=='playing' and self.started_ns is None:self.started_ns=now_ns

    def _event(self,kind,**fields):
        self.events.append((kind,dict(run_id=self.run_id,app=self.app,group=self.group,**fields)))

    def _confirm(self, now_ns, *, undo=False):
        if self.pending is None:return
        event=self.pending
        self._event('demo_selection',action='undo' if undo else 'confirmed',event_ns=now_ns,
                    selection_id=event['selection_id'],false_activation=undo)
        event['false_activation']=undo
        if undo and event['scored']:
            self.score-=1;self.reactions.pop()
        self.pending=None

    def advance(self,now_ns):
        self.selector.advance(now_ns)
        if self.pending is not None and now_ns-self.pending['event_ns']>self.options.undo_ms*1e6:self._confirm(now_ns)
        if self.phase=='feedback' and now_ns>=self.feedback_until and (not self.speech_pending or now_ns>=self.speech_deadline):
            self.speech_pending=False
            self.phase='playing';self._configure()
        if self.app=='game' and self.started_ns is not None and self.phase=='playing' and now_ns-self.started_ns>=self.options.round_s*1e9:
            self.phase='finished';self._configure()
        if self.phase=='finished' and not self._reported and self.pending is None:
            self._summary(now_ns,'completed')

    def feed(self, point,capture_ns,now_ns,*,valid=True,sequence=None):
        self.advance(now_ns)
        if sequence is not None:
            if sequence<=self.last_sequence:return None
            self.last_sequence=sequence
        if self.phase not in {'practice','playing'} or self.presented_ns is None or capture_ns<self.presented_ns:return None
        result=self.selector.feed(point,capture_ns,now_ns,valid=valid)
        if result is None:return None
        target=result['target_id'];button=next(b for b in self.buttons if b.id==target)
        self._confirm(now_ns)
        was_practice=self.phase=='practice'
        scored=self.app=='game' and not was_practice and target==self.target_id
        left,top,w,h=button.rect
        event=dict(**result,selection_id=uuid.uuid4().hex,stage='practice' if was_practice else 'application',
                   target=[(left+w/2)/(self.width-1),(top+h/2)/(self.height-1)],
                   reaction_ms=(now_ns-self.presented_ns)/1e6 if scored else None,
                   false_activation=None,scored=scored,hit_expand_ratio=self.options.hit_expand_ratio)
        self.selections.append(event);self.pending=event
        self._event('demo_selection',action='triggered',**event)
        self.flash_until=now_ns+int(self.options.flash_ms*1e6);self.flash_rect=button.rect
        if was_practice:
            self.feedback_text='学会了！';self._feedback(now_ns,self.options.cooldown_ms)
        elif target=='next':
            self.page=(self.page+1)%self.pages;self._configure()
        elif self.app=='board':
            self.feedback_text=self.options.labels[int(target)]
            self.speech_failed=False
            self._feedback(now_ns,self.options.feedback_ms);self.speech_pending=True
            self.speech_deadline=now_ns+int(self.options.speech_timeout_ms*1e6)
        elif scored:
            self.score+=1;self.reactions.append(event['reaction_ms']);self._configure()
        return event

    def _feedback(self,now_ns,duration_ms):
        self.phase='feedback';self.feedback_until=now_ns+int(duration_ms*1e6)
        self.speech_deadline=self.feedback_until;self.speech_pending=False;self._configure()

    def speech_finished(self,success=True):
        self.speech_pending=False;self.speech_failed=not success

    def _summary(self,now_ns,reason):
        self._reported=True
        selections=[e for e in self.selections if e['stage']=='application' and e['target_id']!='next']
        self._event('demo_summary',event_ns=now_ns,reason=reason,completed=reason=='completed',
                    score=self.score,selection_count=len(selections),
                    false_activation_count=sum(e['false_activation'] is True for e in selections),
                    pending_count=sum(e['false_activation'] is None for e in selections),
                    reaction_count=len(self.reactions),mean_reaction_ms=sum(self.reactions)/len(self.reactions) if self.reactions else None,
                    mean_selection_ms=sum(e['selection_ms'] for e in selections)/len(selections) if selections else None,
                    elapsed_ms=0. if self.started_ns is None else (now_ns-self.started_ns)/1e6,
                    hit_expand_ratio=self.options.hit_expand_ratio)

    def close(self,now_ns,reason='escape'):
        if not self.active:return
        undo=reason=='escape' and self.pending is not None and now_ns-self.pending['event_ns']<=self.options.undo_ms*1e6
        if self.pending is not None:
            # Unexpected interruption cannot certify a full undo observation window.
            if undo or now_ns-self.pending['event_ns']>self.options.undo_ms*1e6:self._confirm(now_ns,undo=undo)
        if not self._reported or undo:self._summary(now_ns,reason)
        self.phase='closed';self.reason=reason;self._configure()

    def drain_events(self):
        result,self.events=self.events,[]
        return result


def validate_demo_event(kind,fields):
    """Only known target IDs and numeric results; never communication text."""
    for key in ('run_id','selection_id'):
        if key=='selection_id' and kind!='demo_selection':continue
        value=fields.get(key)
        if not isinstance(value,str) or len(value)!=32 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Demo '+key+' 必须为32位十六进制编号')
    if fields.get('app') not in {'board','game'} or fields.get('group') not in {'B','C'}:raise ValueError('Demo 应用或分组无效')
    event_ns=fields.get('event_ns')
    if isinstance(event_ns,bool) or not isinstance(event_ns,Integral) or event_ns<0:raise ValueError('Demo event_ns 无效')
    if kind=='demo_selection':
        action=fields.get('action')
        if action not in {'triggered','confirmed','undo'}:raise ValueError('Demo 动作无效')
        required={'run_id','app','group','event_ns','selection_id','action','false_activation'}
        if action=='triggered':required|={'target_id','target','stage','dwell_ms','selection_ms','reaction_ms','capture_ns','scored','hit_expand_ratio'}
        if set(fields)!=required:raise ValueError('Demo 动作字段不完整或包含未允许的数据')
        if action=='triggered':
            if fields.get('target_id') not in {'0','1','2','3','4','5','practice','next'}:raise ValueError('Demo 目标无效')
            if fields.get('stage') not in {'practice','application'}:raise ValueError('Demo 阶段无效')
            point=fields.get('target')
            if not isinstance(point,(list,tuple)) or len(point)!=2 or any(not 0<=finite(v,'target')<=1 for v in point):raise ValueError('Demo target 必须为屏幕归一化坐标')
            for name in ('dwell_ms','selection_ms','hit_expand_ratio'):
                if finite(fields.get(name),name)<0:raise ValueError('Demo 时长/比例必须非负')
            value=fields.get('reaction_ms')
            if value is not None and finite(value,'reaction_ms')<0:raise ValueError('Demo reaction_ms 无效')
            capture=fields.get('capture_ns')
            if isinstance(capture,bool) or not isinstance(capture,Integral) or not 0<=capture<=event_ns:raise ValueError('Demo capture_ns 无效')
            if fields.get('false_activation') is not None or type(fields.get('scored')) is not bool:raise ValueError('Demo 触发尚待确认')
        elif fields.get('false_activation') is not (action=='undo'):raise ValueError('Demo 确认/撤销标记无效')
    else:
        if fields.get('reason') not in {'completed','escape','window_closed','fullscreen_exited','camera_paused','prediction_error',
                                      'screen_changed','conditions_changed','overlay_closed','recording_failed'}:
            raise ValueError('Demo 结束原因无效')
        if type(fields.get('completed')) is not bool:raise ValueError('Demo completed 无效')
        for name in ('score','selection_count','false_activation_count','pending_count','reaction_count'):
            value=fields.get(name)
            if isinstance(value,bool) or not isinstance(value,Integral) or value<0:raise ValueError('Demo 计数无效')
        for name in ('mean_reaction_ms','mean_selection_ms','elapsed_ms','hit_expand_ratio'):
            value=fields.get(name)
            if value is None and name.startswith('mean_'):continue
            if finite(value,name)<0:raise ValueError('Demo 时长/比例无效')
