import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/vocare-app.jsx', import.meta.url), 'utf8');
const speech = vm.createContext({ setTimeout, clearTimeout });
vm.runInContext(source.slice(source.indexOf('const SPEECH_LOCALE ='), source.indexOf('function OfflineLiveScreen(')), speech);

function recognizer({ transcript = 'final transcript', finish = true, error = false } = {}) {
  const handlers = new Map();
  let cached = '', forced = 0;
  return {
    handlers, get forced() { return forced; },
    async addListener(event, callback) {
      handlers.set(event, callback);
      return { remove: async () => handlers.delete(event) };
    },
    async start() { cached = ''; return {}; },
    async stop() {
      if (finish) setTimeout(() => {
        cached = transcript;
        if (error) handlers.get('error')?.({ message: 'Recognition unavailable' });
        handlers.get('readyForNextSession')?.({ sessionId: 1 });
      }, 5);
    },
    async getLastPartialResult() { return { available: Boolean(cached), text: cached }; },
    async forceStop() { forced++; },
  };
}

test('offline capture waits for final native result even with no partial event', async () => {
  const sr = recognizer();
  const capture = speech.createOfflineSpeechCapture(sr, () => {}, 200);
  await capture.start('en-US');
  assert.equal(await capture.stop(), 'final transcript');
  assert.equal(sr.handlers.size, 0);
});

test('consecutive captures do not reuse an earlier transcript', async () => {
  for (const transcript of ['first utterance', '', 'third utterance']) {
    const sr = recognizer({ transcript });
    const capture = speech.createOfflineSpeechCapture(sr, () => {}, 200);
    await capture.start('en-US');
    assert.equal(await capture.stop(), transcript);
  }
});

test('timeout forces native cleanup and rejects instead of translating stale partials', async () => {
  const sr = recognizer({ finish: false });
  const capture = speech.createOfflineSpeechCapture(sr, () => {}, 10);
  await capture.start('en-US');
  sr.handlers.get('partialResults')({ matches: ['unfinished'] });
  await assert.rejects(capture.stop(), /timed out/);
  assert.equal(sr.forced, 1);
  assert.equal(sr.handlers.size, 0);
});

test('native errors are surfaced and listeners removed', async () => {
  const sr = recognizer({ error: true });
  const capture = speech.createOfflineSpeechCapture(sr, () => {}, 200);
  await capture.start('en-US');
  await assert.rejects(capture.stop(), /Recognition unavailable/);
  assert.equal(sr.handlers.size, 0);
});

test('cancelling capture prevents late results from reaching the screen', async () => {
  const sr = recognizer();
  const capture = speech.createOfflineSpeechCapture(sr, () => assert.fail('late partial'), 200);
  await capture.start('en-US');
  await capture.cancel();
  await assert.rejects(capture.stop(), /cancelled/);
  assert.equal(sr.handlers.size, 0);
});

test('voice selection skips network voices and uses a local locale match', async () => {
  const tts = { getSupportedVoices: async () => ({ voices: [
    { lang: 'en-US', localService: false },
    { lang: 'en-AU', localService: true },
    { lang: 'en-US', localService: true },
  ] }) };
  assert.equal(await speech.localTtsVoice(tts, 'en-US'), 2);
  await assert.rejects(speech.localTtsVoice(tts, 'ja-JP'), /No offline voice/);
});

test('Android offline speech avoids the worker-thread on-device availability probe', async () => {
  let unsafeProbeCalls = 0;
  const sr = {
    available: async () => ({ available: true }),
    isOnDeviceRecognitionAvailable: async () => { unsafeProbeCalls++; return { available: true }; },
  };
  await speech.ensureOfflineSpeechAvailable(sr, 'en-US', 'android');
  assert.equal(unsafeProbeCalls, 0);
});

test('non-Android offline speech retains the explicit on-device availability check', async () => {
  let probeCalls = 0;
  const sr = {
    isOnDeviceRecognitionAvailable: async () => { probeCalls++; return { available: false }; },
  };
  await assert.rejects(speech.ensureOfflineSpeechAvailable(sr, 'en-US', 'ios'), /not available/);
  assert.equal(probeCalls, 1);
});

test('Android Mandarin recognition uses the installed canonical Mandarin locale', () => {
  assert.equal(vm.runInContext("recognitionLocale('zh', 'android')", speech), 'cmn-Hans-CN');
  assert.equal(vm.runInContext("recognitionLocale('zh', 'ios')", speech), 'zh-CN');
  assert.equal(vm.runInContext("recognitionLocale('en', 'android')", speech), 'en-US');
});

function payments({ proof = { tier: 'pro', verified: true }, ackFails = false, status = 'purchased' } = {}) {
  let acknowledgements = 0;
  const plugin = {
    currentEntitlement: async () => ({ active: true, purchaseToken: 'test-token', acknowledged: false }),
    purchase: async () => ({ status, purchaseToken: 'test-token' }),
    acknowledge: async () => { acknowledgements++; if (ackFails) throw new Error('ack failed'); },
  };
  const context = vm.createContext({
    window: { Capacitor: { Plugins: { VocarePurchases: plugin } } }, console,
    nativePlatform: () => 'android', activateEntitlement: async () => proof, fetchBalance: async () => proof,
  });
  vm.runInContext(source.slice(source.indexOf('function purchasesPlugin('), source.indexOf('// ── Ads (free tier only)')), context);
  return { context, plugin, get acknowledgements() { return acknowledgements; } };
}

test('restored and resumed verified purchases are acknowledged', async () => {
  const p = payments();
  assert.equal((await p.context.syncStoreEntitlement()).tier, 'pro');
  assert.equal(p.acknowledgements, 1);
});

test('cached Pro status without current verification never acknowledges payment', async () => {
  const p = payments({ proof: { tier: 'pro', verified: false } });
  await p.context.syncStoreEntitlement();
  assert.equal(p.acknowledgements, 0);
});

test('acknowledgement failure remains retryable and does not report success', async () => {
  const p = payments({ ackFails: true });
  await assert.rejects(p.context.syncStoreEntitlement(), /ack failed/);
});

test('purchase cancellation and pending status do not activate Pro', async () => {
  for (const status of ['pending', 'cancelled']) {
    const p = payments({ status });
    assert.equal((await p.context.purchasePro()).status, status);
    assert.equal(p.acknowledgements, 0);
  }
});

test('purchased response without a receipt is rejected', async () => {
  const p = payments();
  p.plugin.purchase = async () => ({ status: 'purchased' });
  await assert.rejects(p.context.purchasePro(), /receipt_missing/);
});
