// Preview never imports the live bridge client or requests any input devices.
const preview=new URLSearchParams(location.search).has('preview');
import(preview?'./preview.mjs':'./app.mjs').catch(error=>{
 document.getElementById('gate').hidden=false;
 document.getElementById('message').textContent='页面启动失败：'+error.message;
});
