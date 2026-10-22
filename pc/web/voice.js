// 语音设置和结果使用工作台同一个本地 API，不在浏览器采集麦克风。
let latest=null, dirty=false, applying=false;
const $=s=>document.querySelector(s);
const labels={disabled:'未启用',waiting:'等待连接',loading_model:'正在加载模型…',subscribing:'正在准备录音…',connecting:'正在订阅',connected:'等待 KEY4',idle:'等待 KEY4',recording:'正在录音',sending:'正在传输',complete:'录音已接收',error:'语音异常'};
export function renderVoice(voice, connected=false) {
  if(!voice)return;
  latest=voice;
  if(!dirty && !applying){
    $('#voice-enabled').checked=voice.settings.enabled;
    $('#voice-transcribe').checked=voice.settings.transcribe;
    $('#voice-type').checked=voice.settings.type_text;
    $('#voice-model').value=voice.settings.model;
  }
  $('#voice-type').disabled=applying || !$('#voice-transcribe').checked;
  $('#voice-status').textContent=voice.processing?'正在识别…':voice.settings.enabled && !connected?'等待连接':labels[voice.status] || voice.status;
  $('#voice-status').classList.toggle('error',Boolean(voice.last_error));
  $('#voice-error').textContent=voice.last_error || '';
  const list=$('#voice-results');list.replaceChildren();
  if(!voice.results.length){const p=document.createElement('p');p.className='voice-empty';p.textContent='启用语音并连接键盘后，按住 KEY4 开始录音。';list.append(p);}
  voice.results.forEach(result=>{
    const item=document.createElement('article');item.className='voice-result';
    const p=document.createElement('p');p.textContent=result.text===null?'录音已保存':result.text || '未检测到语音';
    const info=document.createElement('small');info.textContent=new Date(result.time*1000).toLocaleTimeString()+' · '+(result.error?'自动输入失败：'+result.error:result.typed?'已输入当前窗口':result.text===null?'仅保存录音':'未自动输入');
    item.append(p,info);
    if(result.text){const copy=document.createElement('button');copy.className='text-button';copy.textContent='复制';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(result.text);copy.textContent='已复制';}catch{copy.textContent='请选择文字复制';}});item.append(copy);}
    list.append(item);
  });
}
export function initVoice(api,notify){
  $('#voice-form').addEventListener('change',()=>{
    dirty=true;
    if(!$('#voice-transcribe').checked)$('#voice-type').checked=false;
    $('#voice-type').disabled=!$('#voice-transcribe').checked;
  });
  $('#voice-form').addEventListener('submit',async event=>{
    event.preventDefault();if(applying)return;
    const options={enabled:$('#voice-enabled').checked,transcribe:$('#voice-transcribe').checked,type_text:$('#voice-type').checked,model:$('#voice-model').value};
    applying=true;$('#voice-form').querySelectorAll('input,select,button').forEach(e=>e.disabled=true);
    try{
      const result=await api('voice',options);dirty=false;latest=result;
      notify(options.enabled?'语音设置已应用，等待模型和蓝牙就绪。':'语音已停用，快捷键配置仍可使用。');
    }catch(error){notify(error.message,true);}
    finally{applying=false;$('#voice-form').querySelectorAll('input,select,button').forEach(e=>e.disabled=false);if(latest)renderVoice(latest,latest.connected);}
  });
}
