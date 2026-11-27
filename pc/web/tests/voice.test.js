import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { initVoice } from '../voice.js';

test('shows draft text during recording and keeps confirmed text visible', async t => {
  const elements = new Map();
  let resultRedraws = 0;
  const createElement = () => ({
    textContent: '',
    classList: { toggle() {} },
    addEventListener() {},
    append() {},
    replaceChildren() { resultRedraws++; },
  });
  const previousDocument = globalThis.document;
  globalThis.document = {
    createElement,
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, createElement());
      return elements.get(selector);
    },
  };
  t.after(() => { globalThis.document = previousDocument; });

  let requests = 0;
  const stop = initVoice(async path => {
    assert.equal(path, 'voice');
    requests++;
    return {
      connected: true,
      voice: {
        connected: true,
        status: 'recording',
        settings: { enabled: true, transcribe: true },
        confirmed: requests < 2 ? '' : '第一句。',
        partial: requests < 2 ? '第一' : '第二',
        results: [],
      },
    };
  }, () => {});
  t.after(stop);

  await delay(0);
  assert.equal(elements.get('#voice-live').hidden, false);
  assert.equal(elements.get('#voice-partial').textContent, '第一…');
  for (let attempts = 0; requests < 2 && attempts < 30; attempts++) await delay(20);
  assert.equal(elements.get('#voice-confirmed').textContent, '第一句。');
  assert.equal(elements.get('#voice-partial').textContent, '第二…');
  assert.equal(resultRedraws, 1);
});
