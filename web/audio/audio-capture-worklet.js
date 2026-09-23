import { Pcm16Resampler } from "./pcm-capture.mjs";

class VoiceStudyAudioCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.mode = "disabled";
    this.generationId = 1;
    this.frameSamples = 480;
    this.preRollSamples = 7_200;
    this.pending = [];
    this.pendingSamples = 0;
    this.resampler = new Pcm16Resampler(sampleRate, 24_000);
    this.port.onmessage = (event) => {
      if (event.data?.type === "configure") {
        const previousMode = this.mode;
        this.mode = event.data.mode;
        this.generationId = event.data.generationId;
        if (this.mode === "disabled") {
          this.pending.length = 0;
          this.pendingSamples = 0;
          this.resampler.reset();
        } else if (this.mode === "capturing" && previousMode === "armed") {
          this.emitFrames();
        }
      } else if (event.data?.type === "flush") {
        if (this.mode === "capturing" && this.pendingSamples > 0) {
          const padding = new Int16Array(this.frameSamples - this.pendingSamples);
          this.pending.push(padding);
          this.pendingSamples += padding.length;
          this.emitFrames();
        }
        this.mode = event.data.nextMode || "armed";
        this.port.postMessage({
          type: "flushed",
          generationId: this.generationId,
          requestId: event.data.requestId,
        });
      }
    };
  }

  emitFrames() {
    while (this.pendingSamples >= this.frameSamples) {
      const frame = new Int16Array(this.frameSamples);
      let offset = 0;
      while (offset < frame.length) {
        const head = this.pending[0];
        const count = Math.min(head.length, frame.length - offset);
        frame.set(head.subarray(0, count), offset);
        offset += count;
        this.pendingSamples -= count;
        if (count === head.length) {
          this.pending.shift();
        } else {
          this.pending[0] = head.subarray(count);
        }
      }
      this.port.postMessage(
        {
          type: "audio",
          generationId: this.generationId,
          pcm: frame.buffer,
        },
        [frame.buffer],
      );
    }
  }

  process(inputs) {
    if (this.mode === "disabled") {
      return true;
    }
    const mono = inputs[0]?.[0];
    if (!mono?.length) {
      return true;
    }
    const pcm = this.resampler.process(mono);
    if (pcm.length) {
      this.pending.push(pcm);
      this.pendingSamples += pcm.length;
      if (this.mode === "capturing") {
        this.emitFrames();
      } else {
        while (this.pendingSamples > this.preRollSamples && this.pending.length) {
          const excess = this.pendingSamples - this.preRollSamples;
          const head = this.pending[0];
          if (head.length <= excess) {
            this.pending.shift();
            this.pendingSamples -= head.length;
          } else {
            this.pending[0] = head.subarray(excess);
            this.pendingSamples -= excess;
          }
        }
      }
    }
    return true;
  }
}

registerProcessor("voice-study-audio-capture", VoiceStudyAudioCaptureProcessor);
