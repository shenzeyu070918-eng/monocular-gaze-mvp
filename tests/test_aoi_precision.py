from dataclasses import replace
import unittest
import numpy as np,cv2
from gaze_core.attention.aoi import blocks,primary_regions,precision_groups,AOIStatistics
from gaze_core.attention.settings import AttentionSettings
from gaze_core.reporting.labels import region_label


class PrecisionAOITests(unittest.TestCase):
    def test_dense_table_minimum_size_and_cap_and_duration(self):
        pixels=np.full((900,1200,3),255,np.uint8);lines=[]
        for row in range(12):
            for col in range(5):
                x,y=40+col*225,30+row*66
                cv2.line(pixels,(x,y+45),(x+210,y+45),(80,95,110),1)
                lines.append(dict(text='9.10s' if col else '自动',rect=[x/1200,y/900,(x+92)/1200,(y+24)/900],confidence=np.float64(.95)))
        scale=dict(sigma_x=.06,sigma_y=.10);regions=blocks(lines,pixels,scale,AttentionSettings());mains=primary_regions(regions)
        self.assertLessEqual(len(mains),12);self.assertGreater(len(mains),0)
        self.assertTrue(all(r['rect'][2]-r['rect'][0]>=.12-1e-12 and r['rect'][3]-r['rect'][1]>=.2-1e-12 for r in mains))
        stats=AOIStatistics(regions,scale,80)
        stats.add(dict(start_ns=0,end_ns=1000000000,center=[np.float64(.5),np.float64(.5)]));s=stats.snapshot()
        self.assertAlmostEqual(sum(r['soft_s'] for r in s['regions'])+s['background_soft_s']+s['outside_probability_s'],1)
    def test_number_cap_even_if_cells_meet_error_size(self):
        mains=[dict(id=str(i),rect=[(i%8)/8,(i//8)/5,(i%8)/8+.10,(i//8)/5+.15],role='primary',kind='text_block_candidate',source='ocr') for i in range(40)]
        groups,_,mapping,_=precision_groups(mains,dict(sigma_x=.01,sigma_y=.01),AttentionSettings(aoi_max_primary=7))
        self.assertEqual(len(groups),7);self.assertEqual(set(mapping),{str(i) for i in range(40)})
    def test_poster_image_label_and_names_and_single_tiny_region(self):
        pixels=np.full((900,700,3),255,np.uint8);cv2.rectangle(pixels,(30,180),(670,760),(100,120,145),3)
        lines=[dict(text='图内说明',confidence=.99,rect=[.1,.75,.25,.78])]
        mains=primary_regions(blocks(lines,pixels,dict(sigma_x=.05,sigma_y=.05),AttentionSettings()))
        self.assertEqual(len(mains),1);self.assertTrue(mains[0]['image_like']);self.assertTrue(region_label(mains[0],1).startswith('图片：'))
        self.assertEqual(region_label(dict(image_like=True),2),'图片区域 2')
        self.assertEqual(region_label(dict(source='manual',name='主图',image_like=True),2),'主图')
        groups,children,_,_=precision_groups([dict(id='tiny',kind='text_block_candidate',rect=[.1,.1,.12,.12])],dict(sigma_x=.1,sigma_y=.2),AttentionSettings())
        self.assertEqual(groups[0]['rect'],[0.,0.,1.,1.]);self.assertEqual(children[0]['rect'],[.1,.1,.12,.12])

    def test_text_free_image_is_one_honest_image_region(self):
        pixels=np.random.default_rng(7).integers(0,256,(240,320,3),dtype=np.uint8)
        mains=primary_regions(blocks([],pixels,dict(sigma_x=.1,sigma_y=.1),AttentionSettings()))
        self.assertEqual(len(mains),1);self.assertEqual(mains[0]['rect'],[0.,0.,1.,1.]);self.assertEqual(region_label(mains[0],1),'图片区域 1')
