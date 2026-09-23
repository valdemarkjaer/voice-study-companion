export class GenerationPcmRingBuffer {
  constructor({ capacitySamples = 24_000 * 60, generationId = 1 } = {}) {
    if (!Number.isSafeInteger(capacitySamples) || capacitySamples < 1) {
      throw new RangeError("capacitySamples must be a positive integer");
    }
    if (!Number.isSafeInteger(generationId) || generationId < 1) {
      throw new RangeError("generationId must be a positive integer");
    }
    this.capacitySamples = capacitySamples;
    this.generationId = generationId;
    this.chunks = [];
    this.headOffset = 0;
    this.queuedSamples = 0;
  }

  enqueue(generationId, pcm) {
    if (!(pcm instanceof Int16Array) || pcm.length === 0) {
      throw new TypeError("pcm must be a non-empty Int16Array");
    }
    if (generationId < this.generationId) {
      return false;
    }
    if (generationId > this.generationId) {
      if (generationId !== this.generationId + 1) {
        throw new RangeError("generation must advance contiguously");
      }
      this.reset(generationId);
    }
    if (this.queuedSamples + pcm.length > this.capacitySamples) {
      throw new RangeError("pcm playback queue capacity exceeded");
    }
    this.chunks.push(pcm);
    this.queuedSamples += pcm.length;
    return true;
  }

  cancel(generationId, nextGenerationId) {
    if (
      generationId !== this.generationId ||
      nextGenerationId !== generationId + 1
    ) {
      return false;
    }
    this.reset(nextGenerationId);
    return true;
  }

  reset(generationId = this.generationId) {
    if (!Number.isSafeInteger(generationId) || generationId < 1) {
      throw new RangeError("generationId must be a positive integer");
    }
    this.generationId = generationId;
    this.chunks.length = 0;
    this.headOffset = 0;
    this.queuedSamples = 0;
  }

  pull(sampleCount) {
    if (!Number.isSafeInteger(sampleCount) || sampleCount < 1) {
      throw new RangeError("sampleCount must be a positive integer");
    }
    const output = new Float32Array(sampleCount);
    let writeOffset = 0;
    while (writeOffset < sampleCount && this.chunks.length > 0) {
      const head = this.chunks[0];
      const available = head.length - this.headOffset;
      const copyCount = Math.min(available, sampleCount - writeOffset);
      for (let index = 0; index < copyCount; index += 1) {
        output[writeOffset + index] = head[this.headOffset + index] / 32768;
      }
      writeOffset += copyCount;
      this.headOffset += copyCount;
      this.queuedSamples -= copyCount;
      if (this.headOffset === head.length) {
        this.chunks.shift();
        this.headOffset = 0;
      }
    }
    return output;
  }

  get empty() {
    return this.queuedSamples === 0;
  }
}

export class StreamingPcm16Resampler {
  constructor(inputSampleRate, outputSampleRate) {
    if (
      !Number.isSafeInteger(inputSampleRate) ||
      !Number.isSafeInteger(outputSampleRate) ||
      inputSampleRate < 1 ||
      outputSampleRate < 1
    ) {
      throw new RangeError("sample rates must be positive integers");
    }
    this.inputSampleRate = inputSampleRate;
    this.outputSampleRate = outputSampleRate;
    this.remainder = 0;
    this.previousSample = null;
  }

  reset() {
    this.remainder = 0;
    this.previousSample = null;
  }

  outputLength(inputSamples) {
    if (!Number.isSafeInteger(inputSamples) || inputSamples < 0) {
      throw new RangeError("inputSamples must be a non-negative integer");
    }
    return Math.floor(
      (this.remainder + inputSamples * this.outputSampleRate) /
        this.inputSampleRate,
    );
  }

  process(input) {
    if (!(input instanceof Int16Array)) {
      throw new TypeError("input must be an Int16Array");
    }
    if (input.length === 0) {
      return new Int16Array();
    }
    const output = new Int16Array(this.outputLength(input.length));
    let outputOffset = 0;
    for (const currentSample of input) {
      const previousSample = this.previousSample ?? currentSample;
      const previousRemainder = this.remainder;
      const total = previousRemainder + this.outputSampleRate;
      const emitCount = Math.floor(total / this.inputSampleRate);
      this.remainder = total - emitCount * this.inputSampleRate;
      for (let emitted = 1; emitted <= emitCount; emitted += 1) {
        const fraction =
          (emitted * this.inputSampleRate - previousRemainder) /
          this.outputSampleRate;
        const interpolated =
          previousSample + (currentSample - previousSample) * fraction;
        output[outputOffset] = Math.max(
          -32768,
          Math.min(32767, Math.round(interpolated)),
        );
        outputOffset += 1;
      }
      this.previousSample = currentSample;
    }
    if (outputOffset !== output.length) {
      throw new Error("resampler output length invariant violated");
    }
    return output;
  }
}

function validUtteranceId(value) {
  return typeof value === "string" && Boolean(value.trim());
}

/**
 * AudioWorklet-friendly state around the PCM queue.
 *
 * The emitted drain identifies the exact utterance and the last audio frame
 * consumed. A later frame can therefore invalidate a transient underflow on
 * the main thread before playback is acknowledged to the server.
 */
export class GenerationPcmPlayback {
  constructor({
    generationId = 1,
    inputSampleRate = 24_000,
    outputSampleRate = inputSampleRate,
    capacitySamples = outputSampleRate * 60,
  } = {}) {
    this.buffer = new GenerationPcmRingBuffer({
      capacitySamples,
      generationId,
    });
    this.resampler = new StreamingPcm16Resampler(
      inputSampleRate,
      outputSampleRate,
    );
    this.utteranceId = "";
    this.lastAudioSequence = 0;
    this.hadAudio = false;
  }

  begin(generationId, utteranceId) {
    if (generationId !== this.buffer.generationId) {
      return false;
    }
    if (!validUtteranceId(utteranceId)) {
      throw new TypeError("utteranceId must be a non-empty string");
    }
    if (this.utteranceId === utteranceId) {
      return true;
    }
    if (!this.buffer.empty || this.hadAudio) {
      throw new Error("cannot replace active playback");
    }
    this.resampler.reset();
    this.utteranceId = utteranceId;
    this.lastAudioSequence = 0;
    return true;
  }

  enqueue(generationId, utteranceId, audioSequence, pcm) {
    if (
      generationId !== this.buffer.generationId ||
      utteranceId !== this.utteranceId
    ) {
      return false;
    }
    if (!Number.isSafeInteger(audioSequence) || audioSequence < 1) {
      throw new RangeError("audioSequence must be a positive integer");
    }
    if (audioSequence <= this.lastAudioSequence) {
      return false;
    }
    if (audioSequence !== this.lastAudioSequence + 1) {
      throw new Error("playback audio arrived out of order");
    }
    if (!(pcm instanceof Int16Array) || pcm.length === 0) {
      throw new TypeError("pcm must be a non-empty Int16Array");
    }
    const outputLength = this.resampler.outputLength(pcm.length);
    if (this.buffer.queuedSamples + outputLength > this.buffer.capacitySamples) {
      throw new RangeError("pcm playback queue capacity exceeded");
    }
    const resampled = this.resampler.process(pcm);
    const accepted =
      resampled.length === 0 || this.buffer.enqueue(generationId, resampled);
    if (accepted) {
      this.lastAudioSequence = audioSequence;
      if (resampled.length > 0) {
        this.hadAudio = true;
      }
    }
    return accepted;
  }

  pull(sampleCount) {
    const pcm = this.buffer.pull(sampleCount);
    let event = null;
    if (this.hadAudio && this.buffer.empty) {
      this.hadAudio = false;
      event = {
        type: "drained",
        generationId: this.buffer.generationId,
        utteranceId: this.utteranceId,
        audioSequence: this.lastAudioSequence,
      };
    }
    return { pcm, event };
  }

  cancel(generationId, nextGenerationId) {
    const cancelled = this.buffer.cancel(generationId, nextGenerationId);
    if (cancelled) {
      this.resampler.reset();
      this.utteranceId = "";
      this.lastAudioSequence = 0;
      this.hadAudio = false;
    }
    return cancelled;
  }
}
