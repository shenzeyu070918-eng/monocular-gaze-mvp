import json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
import cv2,numpy as np
from gaze_core.attention.aoi import blocks,AOIStatistics,AOI_ALGORITHM_VERSION,primary_regions,intersection,area
from gaze_core.attention.settings import AttentionSettings
from gaze_core.attention.manual_aoi import save_template,read_template,resolve_manual,select_document


def eight_cards():
    image=np.full((800,1200,3),235,np.uint8);rects=[];lines=[]
    for row in range(4):
        for col in range(2):
            a,b=40+col*580,30+row*190;c,d=a+540,b+160
            cv2.rectangle(image,(a,b),(c,d),(80,95,110),2);cv2.rectangle(image,(a+2,b+2),(c-2,d-2),(255,255,255),-1)
            r=[a/1200,b/800,c/1200,d/800];rects.append(r)
            lines.append(dict(text=f'测试块{len(rects)}',rect=[(a+20)/1200,(b+14)/800,(a+180)/1200,(b+36)/800],confidence=np.float64(.98)))
            lines.append(dict(text='+',rect=[(a+260)/1200,(b+75)/800,(a+280)/1200,(b+95)/800],confidence=.9))
    return image,rects,lines


class HierarchyTests(unittest.TestCase):
    def test_eight_cards_and_unranked_children_capture_centers(self):
        image,rects,lines=eight_cards();regions=blocks(lines,image,dict(sigma_x=.06,sigma_y=.08),AttentionSettings());mains=primary_regions(regions)
        self.assertEqual(len(mains),8)
        for rect in rects:
            best=max(mains,key=lambda r:intersection(r['rect'],rect));self.assertGreater(intersection(best['rect'],rect)/max(area(best['rect']),area(rect)),.94)
        children=[r for r in regions if r.get('role')=='child'];self.assertEqual(len(children),8);self.assertTrue(all(not r['ranked'] for r in children));self.assertTrue(all(r['text']!='+' for r in regions))
        stats=AOIStatistics(regions,dict(sigma_x=.06,sigma_y=.08),80,1.5)
        for i,r in enumerate(rects):stats.add(dict(start_ns=i*1000000000,end_ns=(i+1)*1000000000,center=[np.float64((r[0]+r[2])/2),(r[1]+r[3])/2]))
        result=stats.snapshot();self.assertEqual(len(result['regions']),8);self.assertTrue(all(r['hard_s']==1 for r in result['regions']))
        self.assertAlmostEqual(sum(r['soft_s'] for r in result['regions'])+result['background_soft_s']+result['outside_probability_s'],8)
    def test_columns_and_symbol_fragments_not_merged(self):
        image=np.zeros((800,1200,3),np.uint8);lines=[]
        for x in (.1,.6):
            for y in (.1,.16):lines.append(dict(text='内容段落',rect=[x,y,x+.25,y+.04],confidence=.9))
        for text in ('+','十','!','a'):lines.append(dict(text=text,rect=[.45,.2,.48,.24],confidence=.9))
        mains=primary_regions(blocks(lines,image,dict(sigma_x=.1,sigma_y=.05),AttentionSettings()))
        self.assertEqual(len(mains),2);self.assertTrue(all(r['rect'][2]-r['rect'][0]<.3 for r in mains))
    def test_manual_exact_empty_and_sha_templates(self):
        image,_,lines=eight_cards();manual=[dict(id='hero',name='主图',rect=[.1,.1,.2,.2])]
        r=blocks(lines,image,dict(sigma_x=.1,sigma_y=.1),AttentionSettings(),manual);self.assertEqual(primary_regions(r)[0]['rect'],manual[0]['rect']);self.assertEqual(primary_regions(r)[0]['name'],'主图')
        self.assertEqual(blocks(lines,image,dict(sigma_x=.1,sigma_y=.1),AttentionSettings(),[]),[])
        with tempfile.TemporaryDirectory() as root:
            digest='a'*64;path=save_template(root,digest,manual);doc=read_template(root,digest)
            selected,source=resolve_manual(dict(mode='stimulus',stimulus=dict(sha256=digest)),'scene',None,{},root)
            self.assertEqual(source,'stimulus_template');self.assertEqual(selected[0]['name'],'主图')
            self.assertIsNone(read_template(root,'b'*64))
            with self.assertRaises(ValueError):select_document(doc,'scene',dict(mode='stimulus',stimulus=dict(sha256='b'*64)))
            save_template(root,digest,[]);selected,_=resolve_manual(dict(mode='stimulus',stimulus=dict(sha256=digest)),'scene',None,{},root);self.assertEqual(selected,[])
    def test_old_cache_rebind_version_without_new_ocr(self):
        from report_fixtures import session
        from gaze_core.attention.storage import SessionStore
        from gaze_core.attention.processing import process_session
        with tempfile.TemporaryDirectory() as root:
            path=session(root);store=SessionStore.existing(path,100_000_000)
            try:
                summary=process_session(store,AttentionSettings(),reocr=False,prefix='derived/v2/')
                self.assertEqual(summary['aoi_algorithm_version'],AOI_ALGORITHM_VERSION)
                data=json.loads((path/'derived/v2/aoi.json').read_text());self.assertEqual(data['schema_version'],2);self.assertEqual(data['algorithm_version'],AOI_ALGORITHM_VERSION)
            finally:store.close()

    def test_illustration_ellipses_are_not_containers_inside_wide_hero(self):
        image=np.full((800,1200,3),245,np.uint8);cv2.rectangle(image,(60,100),(1140,700),(160,185,205),3)
        cv2.ellipse(image,(600,380),(180,180),0,0,360,(30,60,90),18)
        cv2.ellipse(image,(400,500),(50,100),0,0,360,(30,60,90),-1)
        cv2.rectangle(image,(210,300),(295,450),(30,60,90),3);cv2.rectangle(image,(910,300),(995,450),(30,60,90),3)
        from gaze_core.attention.aoi import visual_containers
        found=visual_containers(image,AttentionSettings())
        self.assertEqual(len(found),1);self.assertGreater(found[0]['rect'][2]-found[0]['rect'][0],.89)
