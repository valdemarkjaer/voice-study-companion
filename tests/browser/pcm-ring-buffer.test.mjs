import assert from "node:assert/strict";
import test from "node:test";

import { Pcm16Resampler } from "../../web/audio/pcm-capture.mjs";
import {
  GenerationPcmPlayback,
  GenerationPcmRingBuffer,
  StreamingPcm16Resampler,
} from "../../web/audio/pcm-ring-buffer.mjs";

function concatenate(chunks) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const output = new Int16Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

test("generation cancellation flushes queued output to actual silence", () => {
  const buffer = new GenerationPcmRingBuffer({ capacitySamples: 32 });
  buffer.enqueue(1, Int16Array.from([32767, -32768, 1000, -1000]));
  assert.equal(buffer.queuedSamples, 4);

  assert.equal(buffer.cancel(1, 2), true);
  assert.equal(buffer.queuedSamples, 0);
  assert.deepEqual([...buffer.pull(8)], Array(8).fill(0));
});

test("long playback drains every sample without a duration timer", () => {
  const input = Int16Array.from({ length: 10_000 }, (_, index) => index % 32000);
  const buffer = new GenerationPcmRingBuffer({ capacitySamples: 20_000 });
  buffer.enqueue(1, input.subarray(0, 4000));
  buffer.enqueue(1, input.subarray(4000));

  const output = [];
  while (!buffer.empty) {
    output.push(...buffer.pull(128));
  }
  assert.equal(output.length, Math.ceil(input.length / 128) * 128);
  for (let index = 0; index < input.length; index += 1) {
    assert.equal(output[index], input[index] / 32768);
  }
  assert.deepEqual(output.slice(input.length), Array(output.length - input.length).fill(0));
});

test("stale playback chunks cannot re-enter a cancelled generation", () => {
  const buffer = new GenerationPcmRingBuffer();
  buffer.cancel(1, 2);
  assert.equal(buffer.enqueue(1, Int16Array.from([1, 2])), false);
  assert.equal(buffer.empty, true);
});

test("worklet drain identifies the utterance and last consumed audio sequence", () => {
  const playback = new GenerationPcmPlayback({ capacitySamples: 16 });
  assert.equal(playback.begin(1, "utterance-1"), true);
  assert.equal(
    playback.enqueue(1, "utterance-1", 1, Int16Array.from([1, 2])),
    true,
  );

  const first = playback.pull(4);
  assert.deepEqual(first.event, {
    type: "drained",
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 1,
  });

  assert.equal(
    playback.enqueue(1, "utterance-1", 2, Int16Array.from([3, 4])),
    true,
  );
  const second = playback.pull(4);
  assert.deepEqual(second.event, {
    type: "drained",
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 2,
  });
});

test("worklet cancellation flushes audio and rejects stale utterance work", () => {
  const playback = new GenerationPcmPlayback({ capacitySamples: 16 });
  playback.begin(1, "utterance-1");
  playback.enqueue(1, "utterance-1", 1, Int16Array.from([1, 2, 3]));

  assert.equal(playback.cancel(1, 2), true);
  assert.equal(
    playback.enqueue(1, "utterance-1", 2, Int16Array.from([4, 5])),
    false,
  );
  assert.equal(playback.pull(4).event, null);

  assert.equal(playback.begin(2, "utterance-2"), true);
  assert.equal(
    playback.enqueue(2, "utterance-1", 1, Int16Array.from([6, 7])),
    false,
  );
  assert.equal(playback.buffer.empty, true);
});

test("capture resamples to 24 kHz PCM16 and clamps the signal", () => {
  const resampler = new Pcm16Resampler(48_000, 24_000);
  const input = Float32Array.from({ length: 960 }, (_, index) => {
    if (index === 0) return 2;
    if (index === 2) return -2;
    return Math.sin(index / 20);
  });
  const output = resampler.process(input);

  assert.ok(output.length >= 479 && output.length <= 480);
  assert.equal(output[0], 32767);
  assert.equal(output[1], -32768);
});

for (const outputSampleRate of [48_000, 44_100]) {
  test(`playback resampling 24 kHz to ${outputSampleRate} preserves duration across chunks`, () => {
    const input = Int16Array.from(
      { length: 480 },
      (_, index) => Math.round(Math.sin(index / 13) * 20_000),
    );
    const whole = new StreamingPcm16Resampler(24_000, outputSampleRate).process(
      input,
    );
    const streamedResampler = new StreamingPcm16Resampler(
      24_000,
      outputSampleRate,
    );
    const streamed = concatenate([
      streamedResampler.process(input.subarray(0, 137)),
      streamedResampler.process(input.subarray(137, 348)),
      streamedResampler.process(input.subarray(348)),
    ]);

    assert.equal(whole.length, outputSampleRate / 50);
    assert.equal(streamed.length, outputSampleRate / 50);
    assert.deepEqual(streamed, whole);
  });
}

test("playback drain follows the effective 48 kHz output sample count", () => {
  const playback = new GenerationPcmPlayback({
    capacitySamples: 48_000,
    inputSampleRate: 24_000,
    outputSampleRate: 48_000,
  });
  playback.begin(1, "resampled-utterance");
  playback.enqueue(
    1,
    "resampled-utterance",
    1,
    Int16Array.from({ length: 240 }, (_, index) => index),
  );

  assert.equal(playback.buffer.queuedSamples, 480);
  assert.equal(playback.pull(479).event, null);
  assert.deepEqual(playback.pull(1).event, {
    type: "drained",
    generationId: 1,
    utteranceId: "resampled-utterance",
    audioSequence: 1,
  });
});
