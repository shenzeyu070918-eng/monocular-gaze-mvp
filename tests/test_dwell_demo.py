import random
import tempfile
import unittest
from dataclasses import replace
import numpy as np
from gaze_core.config import DemoSettings,OverlaySettings,load_settings
from gaze_core.dwell import Button,DwellSelector
from gaze_core.demo import DemoRun,button_layout


class DwellTests(unittest.TestCase):
    def setUp(self):
        self.options=DemoSettings()
        self.selector=DwellSelector(self.options,(20,20))
        self.selector.set_buttons([Button('a',(0,0,100,100)),Button('b',(120,0,100,100))])

    def feed(self,ms,point=(50,50),valid=True):
        return self.selector.feed(point,np.int64(ms*1_000_000),ms*1_000_000,valid=valid)

    def test_exact_dwell_and_full_fresh_dwell_after_cooldown(self):
        for t in range(0,1000,50):self.assertIsNone(self.feed(t))
        hit=self.feed(1000);self.assertEqual(hit['target_id'],'a')
        for t in range(1050,2800,50):self.assertIsNone(self.feed(t))
        self.assertIsNotNone(self.feed(2800))

    def test_sticky_only_current_button_and_zero_disables(self):
        self.feed(0,(-5,50));self.assertIsNone(self.selector.current)
        self.feed(50);self.feed(100,(109,50))
        self.assertEqual(self.selector.current.id,'a');self.assertEqual(self.selector.progress,.05)
        self.selector=DwellSelector(replace(self.options,hit_expand_ratio=0),(20,20))
        self.selector.set_buttons([Button('a',(0,0,100,100))]);self.feed(0);self.feed(100,(109,50));self.feed(300,(109,50))
        self.assertIsNone(self.selector.current)

    def test_leave_grace_freezes_then_resets(self):
        for t in range(0,501,50):self.feed(t)
        self.feed(550,(115,50));self.feed(650)
        self.assertEqual(self.selector.progress,.5)
        self.feed(700);self.assertEqual(self.selector.progress,.55)
        self.feed(750,(115,50));self.selector.advance(901_000_000)
        self.assertIsNone(self.selector.current)

    def test_loss_freezes_without_backfilling_and_long_loss_resets(self):
        for t in range(0,501,50):self.feed(t)
        self.feed(550,None,False);self.feed(800)
        self.assertEqual(self.selector.progress,.5)
        self.feed(850);self.assertEqual(self.selector.progress,.55)
        self.feed(900,None,False);self.selector.advance(1_301_000_000)
        self.assertIsNone(self.selector.current)

    def test_duplicate_future_old_and_stale_frames_never_advance(self):
        self.feed(0);self.feed(100)
        for _ in range(30):self.feed(100)
        self.selector.feed((50,50),200_000_000,150_000_000)
        self.assertEqual(self.selector.progress,.1)
        self.selector.feed((50,50),150_000_000,700_000_000)
        self.assertEqual(self.selector.progress,0.)
        self.selector.advance(1_101_000_000);self.assertIsNone(self.selector.current)

    def test_current_sticky_wins_overlap(self):
        self.selector=DwellSelector(replace(self.options,hit_expand_ratio=2),(20,20))
        self.selector.set_buttons([Button('a',(0,0,100,100)),Button('b',(120,0,100,100))])
        self.feed(0);self.feed(100,(125,50))
        self.assertEqual(self.selector.current.id,'a')


class DemoTests(unittest.TestCase):
    def make(self,app='board',**options):
        return DemoRun(app,replace(DemoSettings(),**options),1512,982,(100,100),group='B',rng=random.Random(42))

    def select(self,run,target,started=0):
        button=next(b for b in run.buttons if b.id==target);x,y,w,h=button.rect
        run.presented(started*1_000_000)
        result=None
        for t in range(started,started+1001,50):
            result=run.feed((np.float64(x+w/2),np.float64(y+h/2)),t*1_000_000,t*1_000_000)
        return result

    def test_practice_once_then_board_and_undo(self):
        run=self.make();self.assertEqual(run.phase,'practice')
        self.select(run,'practice');run.advance(1_900_000_000)
        self.assertEqual(run.phase,'playing')
        event=self.select(run,'0',2000)
        self.assertEqual(run.phase,'feedback');self.assertEqual(run.feedback_text,'我想喝水')
        run.close(3_500_000_000)
        self.assertTrue(event['false_activation']);self.assertEqual(run.phase,'closed')
        self.assertEqual(run.drain_events()[-1][1]['false_activation_count'],1)

    def test_game_score_undo_and_completion(self):
        run=self.make('game',practice=False,round_s=4)
        target=run.target_id;self.select(run,target)
        self.assertEqual(run.score,1);self.assertNotEqual(run.target_id,target)
        run.close(1_500_000_000);self.assertEqual(run.score,0)
        run=self.make('game',practice=False,round_s=4)
        self.select(run,run.target_id);run.advance(4_000_000_000)
        self.assertEqual(run.phase,'finished')
        summary=run.drain_events()[-1][1]
        self.assertEqual(summary['score'],1);self.assertEqual(summary['mean_reaction_ms'],1000)

    def test_frames_before_paint_or_duplicate_sequence_do_not_trigger(self):
        run=self.make(practice=False);button=run.buttons[0];x,y,w,h=button.rect
        for t in range(0,1201,50):run.feed((x+10,y+10),t*1_000_000,t*1_000_000)
        self.assertFalse(run.selections)
        run.presented(1_200_000_000)
        for t in range(1250,2301,50):run.feed((x+10,y+10),t*1_000_000,t*1_000_000,sequence=1)
        self.assertFalse(run.selections)

    def test_layout_preserves_size_gap_and_dpr_invariance(self):
        for width,height,radii in ((1512,982,(80,90)),(1920,1080,(100,110)),(1280,720,(80,80)),(1000,900,(120,120))):
            cells=button_layout(width,height,radii,DemoSettings())
            for x,y,w,h in cells:
                self.assertGreaterEqual(w,3*radii[0]);self.assertGreaterEqual(h,3*radii[1])
                self.assertGreaterEqual(x,0);self.assertLessEqual(x+w,width)
                self.assertGreaterEqual(y,0);self.assertLessEqual(y+h,height)
            for i,a in enumerate(cells):
                for b in cells[i+1:]:
                    gx=max(b[0]-a[0]-a[2],a[0]-b[0]-b[2]);gy=max(b[1]-a[1]-a[3],a[1]-b[1]-b[3])
                    self.assertTrue(gx>=radii[0]-1e-8 or gy>=radii[1]-1e-8)

    def test_paging_keeps_all_six_labels_and_large_page_control(self):
        run=DemoRun('board',replace(DemoSettings(),practice=False),1000,900,(120,120),group='C')
        self.assertGreater(run.pages,1)
        ids=set()
        for page in range(run.pages):
            run.page=page;run._configure();ids.update(b.id for b in run.buttons if b.id!='next')
            self.assertGreaterEqual(run.buttons[-1].rect[2],360)
        self.assertEqual(ids,set(map(str,range(6))))

    def test_speech_failure_still_completes_feedback(self):
        run=self.make(practice=False);self.select(run,'0');run.speech_finished(False)
        run.advance(3_001_000_000);self.assertEqual(run.phase,'playing');self.assertTrue(run.speech_failed)

    def test_uncalibrated_and_config_invalid(self):
        with self.assertRaisesRegex(ValueError,'校准'):DemoRun('board',DemoSettings(),1512,982,(20,20),group='A')
        for kw in ({'hit_expand_ratio':-1},{'button_font_px':39},{'practice':1},{'labels':['a']},{'loss_reset_ms':float('nan')}):
            with self.assertRaises(ValueError):DemoSettings(**kw)
        self.assertEqual(load_settings(use_local=False).demo.hit_expand_ratio,.5)
        self.assertEqual(OverlaySettings().hotkeys['demo'],'Ctrl+Alt+D')

    def test_demo_events_survive_recording_and_replay_with_numpy_clock(self):
        from test_recorder import metadata
        from gaze_core.config import RecordingSettings
        from gaze_core.recorder import SessionRecorder
        from tools.replay import read_session
        run=self.make('game',practice=False)
        self.select(run,run.target_id);run.close(1_500_000_000)
        events=run.drain_events()
        with tempfile.TemporaryDirectory() as directory:
            recorder=SessionRecorder(metadata(),settings=RecordingSettings(directory,64,2))
            for kind,event in events:
                event['event_ns']=np.int64(event['event_ns']);self.assertTrue(recorder.emit(kind,**event))
            recorder.close('demo-test');session=read_session(recorder.path)
        replayed=[r for r in session.records if r['type'].startswith('demo_')]
        self.assertEqual(len(replayed),len(events))
        self.assertEqual(replayed[0]['target'],events[0][1]['target'])
        self.assertEqual(replayed[0]['dwell_ms'],1000)
        self.assertEqual(replayed[1]['action'],'undo');self.assertTrue(replayed[1]['false_activation'])
        self.assertEqual(session.summary()['event_counts']['demo_selection'],2)

    def test_last_second_game_undo_is_not_prematurely_confirmed(self):
        run=self.make('game',practice=False,round_s=1.5)
        self.select(run,run.target_id);run.advance(1_500_000_000)
        self.assertEqual(run.phase,'finished');self.assertIsNotNone(run.pending)
        run.close(1_700_000_000)
        self.assertEqual(run.score,0);self.assertTrue(run.selections[0]['false_activation'])


if __name__=='__main__':unittest.main()
