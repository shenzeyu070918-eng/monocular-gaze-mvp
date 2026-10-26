import assert from 'node:assert/strict';
import {SceneSelection,sceneDirection,cornerVisual,cornerDiagnostic} from '../static/galaxy/scene-controls.mjs';
const scene=new SceneSelection();
assert.equal(scene.accept({pattern:0,scene_revision:0}),true);
assert.equal(scene.accept({pattern:1,scene_revision:1}),true); // keyboard acknowledgement
assert.equal(scene.accept({pattern:0,scene_revision:0}),false); // older in-flight gaze frame
assert.equal(scene.pattern,1);
assert.equal(scene.accept({pattern:2,scene_revision:2}),true); // next Python dwell continues after keyboard
assert.equal(scene.accept({pattern:0,scene_revision:2}),false);
assert.equal(scene.accept({pattern:0,scene_revision:3}),true);assert.equal(scene.pattern,0);
scene.reset();assert.equal(scene.pattern,0);assert.equal(scene.revision,-1);
for(const [key,direction] of [['ArrowRight',1],[' ',1],['ArrowLeft',-1],['a',0]])assert.equal(sceneDirection({key}),direction);
assert.equal(sceneDirection({key:'ArrowRight',repeat:true}),0);
assert.equal(sceneDirection({key:'ArrowLeft',altKey:true}),0);
const active={corner:1,corner_progress:.6,corner_dwell_ms:900,corner_state:1,corner_reset:0};
assert.deepEqual(cornerVisual(active),{corner:1,progress:.6,opacity:.75});
assert.equal(cornerVisual({...active,corner_state:2}).progress,.6);assert.equal(cornerVisual({...active,corner_state:2}).opacity,.4);
assert.equal(cornerVisual({...active,corner_state:3,corner_progress:1}).opacity,0);
assert.equal(cornerVisual(active,false).opacity,0);
const diagnostic=cornerDiagnostic({xy:[-20,820],gaze_valid:1},{...active,corner_reset:2},[1000,800]);
assert.match(diagnostic,/-0.020 \/ 1.025/);assert.match(diagnostic,/900 ms/);assert.match(diagnostic,/断档/);
console.log('single scene authority/revisions, local key actions, corner grace/fade and unclipped UV diagnostics passed');

assert.match(cornerDiagnostic({xy:[0,0],gaze_valid:0},active,[1000,800]),/视口 uv —.*视线无效/);
