import { initVoice, renderVoice } from './voice.js';
import { DEFAULTS, MODIFIERS, KEYS, clone, combo, fromEvent, setModifier, validate, changed } from './model.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let saved = clone(DEFAULTS), draft = clone(DEFAULTS), selected = 0, activeKey = 0;
let connected = false, busy = false, recording = false, token = '', stopped = false;
let toastTimer, pollTimer, operationGeneration = 0;
let savedRotary=null, rotaryDraft="scroll";
let savedSleep=null, sleepDraft=10;
initVoice(api, notify);
const form = $('#key-form');
const keySelect = $('#normal-key');
for (const key of KEYS) {
  const option = document.createElement('option');
  option.value = key.value; option.textContent = key.name;
  keySelect.append(option);
}

function notify(message, error=false) {
  const notice = $('#notice');
  clearTimeout(toastTimer); notice.textContent = message; notice.hidden = false;
  notice.classList.toggle('error', error);
  toastTimer = setTimeout(()=>{ notice.hidden=true; }, error?9000:4500);
}

async function api(path, data) {
  const controller = new AbortController();
  const timeout = setTimeout(()=>controller.abort(), 45000);
  try {
    const response = await fetch('/api/'+path, {
      method:data===undefined?'GET':'POST', signal:controller.signal,
      headers: {'Content-Type':'application/json', 'X-Keyboard-Token':token},
      ...(data===undefined?{}:{body:JSON.stringify(data)}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '操作未完成，请重试。');
    return result;
  } catch (error) {
    if (error.name==='AbortError') throw new Error('连接超时，请检查键盘电源和蓝牙。');
    if (error instanceof TypeError) throw new Error('本地配置服务未响应，请重新打开 start.cmd。');
    throw error;
  } finally { clearTimeout(timeout); }
}

function stateText(text) {
  const status=$('#connection-status');
  status.replaceChildren(document.createElement('i'), document.createTextNode(text));
  status.classList.toggle('connected',connected);
  status.classList.toggle('busy',busy);
}

function paintOled() {
  const ctx=$('#oled').getContext('2d');
  ctx.setTransform(4,0,0,4,0,0);
  ctx.fillStyle='#0a120e';ctx.fillRect(0,0,128,64);
  ctx.fillStyle='#d6e7ce';ctx.strokeStyle='#d6e7ce';ctx.lineWidth=.5;
  ctx.font='7px monospace';ctx.textBaseline='alphabetic';ctx.fillText('ibook',1,10);
  ctx.font='9px sans-serif';ctx.textAlign='right';ctx.fillText(connected?'已连接':'未连接',126,10);ctx.textAlign='left';
  ctx.fillRect(0,13,128,.5);
  draft.forEach((binding,index)=>{
    const x=index*44;
    ctx.strokeRect(x+.5,16.5,39,27);
    ctx.font='9px sans-serif';ctx.textAlign='center';ctx.fillText(`${index+1} ${binding.label}`,x+20,28,36);
    let chord=combo(binding);ctx.font='6px monospace';
    if (ctx.measureText(chord).width>36) {
      chord=chord.replaceAll('Ctrl+','C+').replaceAll('Shift+','S+').replaceAll('Alt+','A+').replaceAll('Win+','W+');
      ctx.font='5px monospace';
    }
    ctx.fillText(chord,x+20,40,36);
  });
  ctx.textAlign='left';ctx.fillRect(0,47,128,.5);
  ctx.strokeRect(.5,50.5,14,13);ctx.font='7px monospace';ctx.fillText('4',5,60);
  ctx.font='9px sans-serif';ctx.fillText('按住录音',20,59);
}

function render() {
  $('#sleep-minutes').value=String(sleepDraft);
  $('#sleep-minutes').disabled=busy;
  $('#sleep-save').disabled=busy || !connected || savedSleep===null || savedSleep===sleepDraft;
  $('#sleep-hint').textContent=!connected?'连接键盘后读取当前设置。':savedSleep===null?'请烧录支持自动深睡的新版固件。':sleepDraft!==savedSleep?'有未保存的深睡设置。':savedSleep===0?'自动深睡已关闭。':`当前设置：闲置 ${savedSleep} 分钟后深睡。`;

  const voiceSelected=activeKey===3;
  $('#shortcut-editor').hidden=activeKey>=3;
  $('#rotary-editor').hidden=activeKey!==4;
  $('#rotary-key').classList.toggle('selected',activeKey===4);
  $('#rotary-key').setAttribute('aria-pressed',String(activeKey===4));
  $('#rotary-key').disabled=busy;
  $('#rotary-save').disabled=busy || !connected || savedRotary===null || rotaryDraft===savedRotary;
  $$('[name=rotary-mode]').forEach(input=>{input.checked=input.value===rotaryDraft;input.disabled=busy;});
  $('#rotary-caption').textContent=savedRotary==='volume'?'音量 / 静音':'滚动 / 确定';
  $('#rotary-hint').textContent=!connected?'连接键盘后读取当前模式。':savedRotary===null?'需要烧录支持旋钮模式的新版固件，并重新配对。':rotaryDraft!==savedRotary?'有未保存的模式修改。':'当前模式已保存在键盘中。';
  $('#voice-editor').hidden=!voiceSelected;
  $('.preset-strip').hidden=activeKey>=3;
  $('#voice-key').classList.toggle('selected',voiceSelected);
  $('#voice-key').setAttribute('aria-pressed',String(voiceSelected));
  $('#voice-key').disabled=busy;
  const binding=draft[selected];const dirty=changed(draft,saved);const invalid=validate(draft);
  $$('.device-key').forEach((button,index)=>{
    const key=draft[index];button.classList.toggle('selected',index===activeKey);
    button.setAttribute('aria-pressed',String(index===activeKey));
    button.setAttribute('aria-label',`KEY${index+1}，${key.label}，${combo(key)}`);
    button.querySelector('strong').textContent=key.label || '未命名';
    button.querySelector('.key-combo').textContent=combo(key);
    button.disabled=busy;
  });
  $('#selected-number').textContent=String(selected+1).padStart(2,'0');
  $('#editor-title').textContent=`按键 ${selected+1}`;
  if ($('#key-label').value!==binding.label) $('#key-label').value=binding.label;
  keySelect.value=binding.key;
  const chips=$('#combo-chips');chips.replaceChildren();
  combo(binding).split('+').forEach((name,index)=>{
    if(index) {const plus=document.createElement('span');plus.textContent='+';chips.append(plus);}
    const kbd=document.createElement('kbd');kbd.textContent=name;chips.append(kbd);
  });
  $$('[data-mod]').forEach(input=>{input.checked=Boolean(binding.modifiers&MODIFIERS[Number(input.dataset.mod)].mask);});
  $('#validation').textContent=invalid;
  $('#dirty-dot').classList.toggle('dirty',dirty);
  $('#draft-status').textContent=dirty?'有未保存的修改':connected?'与键盘配置一致':'当前为默认配置';
  $('#save').disabled=busy || !connected || !dirty || Boolean(invalid);
  $('#save').textContent=busy?'处理中…':'保存到键盘';
  if(!busy) { const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#arrow');svg.append(use);$('#save').append(svg); }
  $('#discard').disabled=busy || !dirty;$('#reset').disabled=busy;
  $('#connect').disabled=busy || stopped;
  $('#stop').disabled=busy || stopped;
  $('#read').disabled=busy || !connected;$('#disconnect').disabled=busy || !connected;
  $('#save-hint').textContent=busy?'请稍候，正在与键盘通信。':!connected?'连接键盘后，即可保存。':dirty?'保存后立即生效，并在断电后保留。':'修改名称或组合键，开始自定义。';
  form.querySelectorAll('button,input,select').forEach(element=>{element.disabled=busy;});
  $$('#presets button').forEach(button=>{button.disabled=busy;});
  $('#record-combo').classList.toggle('recording',recording);
  $('#record-label').textContent=recording?'等待按键…':'点击录入';
  if(!busy) stateText(stopped?'服务已退出':connected?'已连接':'未连接');
  paintOled();
}

async function operation(action, message='正在处理') {
  if(busy) return;
  operationGeneration++;stopRecording();busy=true;render();stateText(message);
  try { return await action(); }
  catch(error) {notify(error.message,true);}
  finally {busy=false;render();}
}

function stopRecording() {recording=false;$('#record-help').textContent='点击后按下组合键；也可使用下面的选项。';}
function selectKey(index) {
  stopRecording();activeKey=index;if(index<3)selected=index;render();
  if(!matchMedia('(prefers-reduced-motion: reduce)').matches) (index===4 ? $('#rotary-title') : index===3 ? $('#voice-title') : $('#editor-title')).animate([{opacity:.3,transform:'translateY(3px)'},{opacity:1,transform:'translateY(0)'}],{duration:160});
}
$$('.device-key').forEach(button=>button.addEventListener('click',()=>selectKey(Number(button.dataset.key))));
$('#voice-key').addEventListener('click',()=>selectKey(3));
$('#rotary-key').addEventListener('click',()=>selectKey(4));
$('#sleep-settings').addEventListener('click',()=>$('#sleep-dialog').showModal());
$('#sleep-minutes').addEventListener('change',event=>{sleepDraft=Number(event.target.value);render();});
$('#sleep-save').addEventListener('click',()=>operation(async()=>{
  const result=await api('sleep',{minutes:sleepDraft});
  savedSleep=result.sleep_minutes;sleepDraft=savedSleep;
  notify(savedSleep===0?'自动深睡已关闭。':`已保存：闲置 ${savedSleep} 分钟后进入深睡。`);
},'正在保存'));

$$('[name=rotary-mode]').forEach(input=>input.addEventListener('change',()=>{rotaryDraft=input.value;render();}));
$('#rotary-save').addEventListener('click',()=>operation(async()=>{
  const result=await api('rotary',{mode:rotaryDraft});
  savedRotary=result.rotary_mode;rotaryDraft=savedRotary;
  notify('旋钮模式已保存到键盘。');
},'正在保存'));
function updateRotary(config){
  if(!config)return;
  const sleepDirty=savedSleep!==null && savedSleep!==sleepDraft;
  savedSleep=config.sleep_minutes ?? null;
  if(!sleepDirty && savedSleep!==null) {
    sleepDraft=savedSleep;
    const select=$('#sleep-minutes');
    if(![...select.options].some(option=>Number(option.value)===savedSleep)) {
      const option=document.createElement('option');option.value=String(savedSleep);option.textContent=`${savedSleep} 分钟`;select.append(option);
    }
  }
  const dirty=savedRotary!==null && rotaryDraft!==savedRotary;
  savedRotary=config.rotary_mode ?? null;
  if(!dirty && savedRotary!==null)rotaryDraft=savedRotary;
}

$('#key-label').addEventListener('input',event=>{draft[selected].label=event.target.value;render();});
keySelect.addEventListener('change',()=>{stopRecording();draft[selected].key=Number(keySelect.value);render();});
$$('[data-mod]').forEach(input=>input.addEventListener('change',()=>{
  stopRecording();draft[selected]=setModifier(draft[selected],MODIFIERS[Number(input.dataset.mod)],input.checked);render();
}));
$('#clear-mods').addEventListener('click',()=>{stopRecording();draft[selected].modifiers=0;render();});
$('#record-combo').addEventListener('click',()=>{
  recording=!recording;
  $('#record-help').textContent=recording?'按下组合键完成录入；按 Esc 取消。系统保留组合键请使用下方选项。':'点击后按下组合键；也可使用下面的选项。';
  render();
});
document.addEventListener('keydown',event=>{
  if(!recording || busy) return;
  event.preventDefault();event.stopPropagation();
  if(event.repeat) return;
  if(event.code==='Escape') {stopRecording();render();return;}
  const binding=fromEvent(event);
  if(binding) {draft[selected]={...draft[selected],...binding};stopRecording();render();}
},true);
$('#record-combo').addEventListener('blur',()=>{if(recording){stopRecording();render();}});
form.addEventListener('submit',event=>event.preventDefault());
const presets={copy:{label:'复制',modifiers:1,key:6},paste:{label:'粘贴',modifiers:1,key:25},undo:{label:'撤销',modifiers:1,key:29},save:{label:'保存',modifiers:1,key:22},screen:{label:'截图',modifiers:10,key:22}};
$$('#presets button').forEach(button=>button.addEventListener('click',()=>{stopRecording();draft[selected]={id:selected+1,...presets[button.dataset.preset]};render();}));
$('#discard').addEventListener('click',()=>{stopRecording();draft=clone(saved);render();});
$('#reset').addEventListener('click',()=>{stopRecording();draft=clone(DEFAULTS);render();notify('默认配置已放入草稿，点击保存后才会写入键盘。');});

async function searchDevices() {
  const list=$('#device-list');list.textContent='正在查找键盘…';$('#refresh-devices').disabled=true;
  try {
    const {devices}=await api('devices');list.replaceChildren();
    if(!devices.length) {list.textContent='未找到键盘。请确认键盘已开机，并在 Windows 完成蓝牙配对。';return;}
    devices.forEach(device=>{
      const button=document.createElement('button');button.className='device-option';
      const description=document.createElement('span');description.textContent=device.name;
      const small=document.createElement('small');small.textContent=device.address;description.append(small);
      button.append(description,document.createTextNode('→'));list.append(button);
      button.addEventListener('click',()=>{
        $('#device-dialog').close();
        operation(async()=>{
          const keepDraft=changed(draft,saved);
          const result=await api('connect',{address:device.address});
          connected=true;updateRotary(result);saved=clone(result.keys);if(!keepDraft)draft=clone(result.keys);
          notify(keepDraft?'键盘已连接，保留了当前草稿。':'键盘已连接，已读取当前配置。');
        },'正在连接');
      });
    });
  } catch(error) {list.textContent=error.message;}
  finally {$('#refresh-devices').disabled=false;}
}
$('#connect').addEventListener('click',()=>{$('#device-dialog').showModal();searchDevices();});
$('#refresh-devices').addEventListener('click',searchDevices);
$('#help').addEventListener('click',()=>$('#help-dialog').showModal());
$$('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$('#save').addEventListener('click',()=>operation(async()=>{
  const invalid=validate(draft);if(invalid)throw new Error(invalid);
  const result=await api('config',{keys:clone(draft)});
  saved=clone(result.keys);draft=clone(result.keys);notify('已保存到键盘，新的快捷键现在已生效。');
},'正在保存'));
$('#read').addEventListener('click',()=>{
  if(changed(draft,saved) && !confirm('重新读取将丢弃当前未保存的修改，继续吗？'))return;
  operation(async()=>{const result=await api('read',{});saved=clone(result.keys);draft=clone(result.keys);notify('已读取键盘当前配置。');},'正在读取');
});
$('#disconnect').addEventListener('click',()=>operation(async()=>{await api('disconnect',{});connected=false;notify('键盘连接已断开，语音已暂停，页面草稿仍然保留。');},'正在断开'));
$('#stop').addEventListener('click',()=>{
  if(!confirm(changed(draft,saved)?'还有未保存的修改，仍要退出配置服务吗？':'退出工作台并停止语音？已保存的快捷键会继续生效。'))return;
  operation(async()=>{await api('stop',{});connected=false;stopped=true;clearInterval(pollTimer);notify('工作台已退出。下次双击 start.cmd 即可重新打开。');});
});
window.addEventListener('beforeunload',event=>{if((changed(draft,saved) || (savedRotary!==null && rotaryDraft!==savedRotary) || (savedSleep!==null && sleepDraft!==savedSleep)) && !stopped){event.preventDefault();event.returnValue='';}});

async function poll() {
  if(busy || stopped) return;
  const generation=operationGeneration;
  try {
    const status=await api('status');
    if(generation!==operationGeneration || busy || stopped) return;
    if(connected && !status.connected) notify('键盘连接已断开，未保存的草稿仍保留。',true);
    connected=status.connected;updateRotary(status.config);render();renderVoice(status.voice,connected);
  } catch {if(generation===operationGeneration && !busy && !stopped){connected=false;render();stateText('服务未连接');}}
}
render();
try {
  const session=await api('session');token=session.token;
  const status=await api('status');connected=status.connected;
  if(status.config){saved=clone(status.config.keys);draft=clone(saved);updateRotary(status.config);}
  renderVoice(status.voice,connected);
  render();pollTimer=setInterval(poll,3000);
} catch(error) {notify(error.message,true);stateText('服务未连接');}
