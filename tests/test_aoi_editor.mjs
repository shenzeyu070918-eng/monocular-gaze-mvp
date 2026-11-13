import assert from 'node:assert/strict';
import {rectFromPoints,moveRect,resizeRect,hitRegion,fitImage,automaticFramesFillImage,makeDocument,importDocument} from '../static/aoi-editor/logic.mjs';
assert.deepEqual(rectFromPoints([.9,.8],[-.2,.1]),[0,.1,.9,.8]);
assert.deepEqual(moveRect([.1,.2,.4,.5],[2,-2]),[.7,0,1,.3]);
assert.deepEqual(resizeRect([.1,.2,.4,.5],0,[.2,.3]),[.2,.3,.4,.5]);
const scenes=[{id:'one',regions:[{id:'hero',name:'主图',rect:[.2,.2,.7,.7]}]}];
assert.equal(hitRegion(scenes[0].regions,[.5,.5]),0);assert.equal(hitRegion(scenes[0].regions,[.1,.1]),-1);
const meta={mode:'stimulus',stimulus_sha256:'abc'},doc=makeDocument(scenes,meta);
assert.equal(doc.regions[0].name,'主图');assert.equal(doc.stimulus_sha256,'abc');assert.equal(doc.coordinate_space,'image_normalized');
assert.throws(()=>importDocument(doc,scenes,{...meta,stimulus_sha256:'different'}),/SHA256/);
const deleted=importDocument({...doc,regions:[]},scenes,meta);assert.deepEqual(deleted[0].regions,[]);
const screen=makeDocument(scenes,{mode:'screen'});assert.equal(screen.scenes.one[0].id,'hero');assert.equal(screen.coordinate_space,'full_screen_normalized');
assert.throws(()=>makeDocument([{id:'one',regions:[{id:'hero',name:'bad',rect:[NaN,.1,.2,.3]}]}],meta),/无效/);
console.log('Offline AOI editor: normalized draw/move/resize, deletion, document roundtrip and SHA guard passed');


assert.deepEqual(fitImage(1200,1600,900,600),[450,600]);
assert.deepEqual(fitImage(1600,1200,800,900),[800,600]);
const auto=rect=>({automatic:true,rect});
assert.equal(automaticFramesFillImage([auto([0,0,1,1])]),true);
assert.equal(automaticFramesFillImage([auto([0,0,.6,1]),auto([.6,0,1,.5]),auto([.6,.5,1,1])]),true);
assert.equal(automaticFramesFillImage([auto([0,0,.6,1]),auto([.61,0,1,1])]),false);
assert.equal(automaticFramesFillImage([{rect:[0,0,1,1]}]),false);
assert.equal(automaticFramesFillImage([]),false);
assert.deepEqual(makeDocument([{id:'one',regions:[]}],meta).regions,[]);
