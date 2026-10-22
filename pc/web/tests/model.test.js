import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULTS, MODIFIERS, KEYS, clone, combo, fromEvent, setModifier, validate, changed } from '../model.js';
test('draft changes never mutate saved or default configuration',()=>{
  const saved=clone(DEFAULTS),draft=clone(saved);draft[0].label='保存';
  assert.equal(saved[0].label,'复制');assert.equal(DEFAULTS[0].label,'复制');assert.ok(changed(saved,draft));
});
test('captures normal keys and combinations from physical event codes',()=>{
  assert.deepEqual(fromEvent({code:'KeyS',ctrlKey:true,shiftKey:true}),{key:22,modifiers:3});
  assert.deepEqual(fromEvent({code:'ArrowDown',altKey:true}),{key:81,modifiers:4});
  assert.equal(fromEvent({code:'ShiftLeft'}),null);
  assert.equal(new Set(KEYS.map(key=>key.value)).size,KEYS.length);
});
test('right modifiers normalize when edited, other modifiers remain',()=>{
  const binding={...DEFAULTS[0],modifiers:0x12};
  assert.equal(setModifier(binding,MODIFIERS[0],false).modifiers,2);
  assert.equal(setModifier(binding,MODIFIERS[0],true).modifiers,3);
  assert.equal(combo(binding),'Ctrl+Shift+C');
});
test('validates all three bindings and bounded OLED names',()=>{
  assert.equal(validate(clone(DEFAULTS)),'');
  for(const label of ['', '复制按键',' A','😀','<img>']) {
    const draft=clone(DEFAULTS);draft[0].label=label;assert.notEqual(validate(draft),'');
  }
  const draft=clone(DEFAULTS);draft[0].label='Save';assert.equal(validate(draft),'');
});
