export const DEFAULTS = [
  { id: 1, label: '复制', modifiers: 1, key: 6 },
  { id: 2, label: '粘贴', modifiers: 1, key: 25 },
  { id: 3, label: '撤销', modifiers: 1, key: 29 },
];
export const MODIFIERS = [
  { name: 'Ctrl', bit: 1, mask: 17 }, { name: 'Shift', bit: 2, mask: 34 },
  { name: 'Alt', bit: 4, mask: 68 }, { name: 'Win', bit: 8, mask: 136 },
];
const extras = [
  [40,'Enter','Enter'],[41,'Esc','Escape'],[42,'Backspace','Backspace'],[43,'Tab','Tab'],
  [44,'Space','Space'],[45,'−','Minus'],[46,'=','Equal'],[47,'[','BracketLeft'],
  [48,']','BracketRight'],[49,'\\','Backslash'],[51,';','Semicolon'],[52,"'",'Quote'],
  [53,'`','Backquote'],[54,',','Comma'],[55,'.','Period'],[56,'/','Slash'],
  [73,'Insert','Insert'],[74,'Home','Home'],[75,'PageUp','PageUp'],[76,'Delete','Delete'],
  [77,'End','End'],[78,'PageDown','PageDown'],[79,'→','ArrowRight'],[80,'←','ArrowLeft'],
  [81,'↓','ArrowDown'],[82,'↑','ArrowUp'],
];
export const KEYS = [
  ...Array.from({length:26},(_,i)=>({value:4+i,name:String.fromCharCode(65+i),code:'Key'+String.fromCharCode(65+i)})),
  ...Array.from({length:10},(_,i)=>({value:30+i,name:String((i+1)%10),code:'Digit'+((i+1)%10)})),
  ...Array.from({length:12},(_,i)=>({value:58+i,name:'F'+(i+1),code:'F'+(i+1)})),
  ...extras.map(([value,name,code])=>({value,name,code})),
];
export function clone(keys) { return keys.map(key=>({...key})); }
export function combo(binding) {
  return [...MODIFIERS.filter(mod=>binding.modifiers&mod.mask).map(mod=>mod.name),
    KEYS.find(key=>key.value===binding.key)?.name ?? '0x'+binding.key.toString(16).toUpperCase()].join('+');
}
export function fromEvent(event) {
  const key = KEYS.find(key=>key.code===event.code || (event.code==='NumpadEnter' && key.code==='Enter'));
  if (!key) return null;
  return { key:key.value, modifiers:(event.ctrlKey?1:0)|(event.shiftKey?2:0)|(event.altKey?4:0)|(event.metaKey?8:0) };
}
export function setModifier(binding, mod, checked) {
  return {...binding,modifiers:(binding.modifiers&~mod.mask)|(checked?mod.bit:0)};
}
export function validate(keys) {
  if (!Array.isArray(keys) || keys.length!==3) return '需要三个按键配置。';
  for (let i=0;i<keys.length;i++) {
    const key=keys[i];
    if (key.id!==i+1 || !Number.isInteger(key.key) || key.key<4 || key.key>101 ||
        !Number.isInteger(key.modifiers) || key.modifiers<0 || key.modifiers>255) return '组合键配置无效。';
    if (typeof key.label!=='string' || !key.label.trim() || key.label!==key.label.trim()) return '请填写显示名称，首尾不要留空格。';
    if (!/^[\p{Script=Han}A-Za-z0-9 _-]+$/u.test(key.label)) return '名称请使用常用汉字、英文或数字。';
    if ([...key.label].reduce((sum,c)=>sum+(c.codePointAt(0)<128?1:2),0)>4) return '名称最多 2 个汉字或 4 个英文字符。';
  }
  return '';
}
export function changed(a,b) { return JSON.stringify(a)!==JSON.stringify(b); }
